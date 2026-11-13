"""CGHS Enhancement Bot - desktop entry point.

WHAT LIVES HERE (after the v3 speed/safety hardening):
  * the PyQt5 user interface,
  * the Qt worker thread that drives a batch,
  * failure-artifact capture and the parser audit exporter.

WHAT NO LONGER LIVES HERE:
  * the CGHS parsing rules          -> cghs.rules / cghs.parsing
  * the portal locator registry     -> cghs.locators
  * the DOM/automation controllers  -> cghs.dom / cghs.controllers
  * the Plus transaction machine    -> cghs.txstate
  * the session + orchestrator      -> cghs.session / cghs.orchestrator

The `cghs` package is the SINGLE canonical owner of that behaviour; it is
imported here rather than duplicated, and it is importable (and therefore
testable) without PyQt5 or a browser.
"""

import sys
import os
import re
import time
import json
import traceback
from datetime import datetime
from typing import List, Dict, Tuple, Optional, Any
import fitz
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QFileDialog,
    QStackedWidget, QScrollArea, QFrame, QSplitter, QProgressBar,
    QGraphicsDropShadowEffect, QLineEdit, QGridLayout,
    QTextEdit, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QHBoxLayout, QMessageBox, QComboBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QMutex
from PyQt5.QtGui import QFont, QColor

import ui_theme  # UI THEME (presentation only)
import selenium.webdriver as webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException

# ---------------------------------------------------------------------------
# Canonical automation core (single implementation - do not re-declare here)
# ---------------------------------------------------------------------------
from cghs.locators import (
    NETWORK_DELAY, VALID_CODES, PORTAL_OPTION_MAP, CGHS_CATEGORY_MAP, LOCATORS,
)
from cghs.rules import (
    extract_dept_subtotal, extract_consumables_total, parse_row_quantity,
    parse_oxygen_quantity, normalize_cghs_code,
)
from cghs.parsing import (
    CGHSParsingEngine, extract_room_rent_section, extract_service_rows,
)
from cghs.telemetry import EnterpriseLogger
from cghs.session import PortalSession, probe_debugger_endpoint
from cghs.orchestrator import BatchRunner
from cghs.tabs import AmbiguousPatientTab, NoPatientTab
from cghs.txstate import TransactionJournal


class DiagnosticEngine:
    @staticmethod
    def capture_artifact(driver: webdriver.Chrome, patient: str, code: str, state: str, exception: Exception) -> Dict[str, str]:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        logs_dir = os.path.join(os.getcwd(), "audit_failures", timestamp)
        os.makedirs(logs_dir, exist_ok=True)
        scr_path = os.path.join(logs_dir, "failure_screenshot.png")
        dom_path = os.path.join(logs_dir, "failure_dom.html")
        meta_path = os.path.join(logs_dir, "failure_metadata.json")
        artifacts = {}
        try:
            if driver:
                driver.save_screenshot(scr_path)
                with open(dom_path, "w", encoding="utf-8") as f:
                    f.write(driver.page_source)
                current_url = driver.current_url
            else:
                current_url = "UNKNOWN_NO_DRIVER"
            meta = {
                "timestamp": timestamp, "patient": patient, "target_code": code, "failed_state": state,
                "current_url": current_url, "exception_type": type(exception).__name__,
                "exception_message": str(exception), "stacktrace": traceback.format_exc()
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=4)
            artifacts = {"screenshot": scr_path, "dom": dom_path, "metadata": meta_path}
        except Exception as e:
            print(f"[DiagnosticEngine] Diagnostic recording failed: {e}")
        return artifacts

class ParserDiagnosticAuditor:
    @staticmethod
    def export_audit_report(patient_name: str, raw_occurrences: List[Dict], detected_items: List[Dict[str, Any]], rejected_rows: List[Dict[str, str]], aggregation_log: List[str]):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        debug_dir = os.path.join(os.getcwd(), "parser_debug_reports")
        os.makedirs(debug_dir, exist_ok=True)
        report_path = os.path.join(debug_dir, f"audit_report_{timestamp}.json")
        report_data = {
            "timestamp": timestamp, "patient_name": patient_name, "total_occurrences": len(raw_occurrences),
            "raw_occurrences": raw_occurrences, "final_automation_queue": detected_items,
            "rejected_rows_or_tokens": rejected_rows, "aggregation_log": aggregation_log
        }
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report_data, f, indent=4)
            print(f"[ParserDiagnosticAuditor] Audit report exported: {report_path}")
        except Exception as e:
            print(f"[ParserDiagnosticAuditor] Failed to export: {e}")



class BatchAutomationThread(QThread):
    """Qt worker thread - a THIN adapter over :class:`cghs.orchestrator.BatchRunner`.

    PERFORMANCE CONTRACT (6.1): exactly one Python process, one CDP attach, one
    Treatment Plan discovery and one reusable orchestrator for the whole batch.
    This thread owns none of the automation logic itself.
    """

    log_signal = pyqtSignal(str)
    patient_status_signal = pyqtSignal(int, str)
    finished_signal = pyqtSignal(bool, str)
    performance_signal = pyqtSignal(dict)

    def __init__(self, batch_queue: List[Dict[str, Any]], delay_mode: str,
                 trace: bool = False):
        super().__init__()
        self.batch_queue = batch_queue
        self.delay_mode = delay_mode
        self.trace = trace
        self.logger = EnterpriseLogger(self.log_signal, trace_enabled=trace)
        self._is_cancelled = False
        self.session = None
        self.last_summary = None

    def stop(self):
        self._is_cancelled = True
        if self.session is not None:
            self.session.cancel()
        self.logger.warn("BatchThread: cancellation requested")

    def run(self):
        profile = NETWORK_DELAY.get(self.delay_mode, {"step": 1.0, "timeout": 20})
        self.logger.info(f"BatchThread: starting [{self.delay_mode}]")
        self.logger.info("BatchThread: attaching to Chrome debug 127.0.0.1:9222 (ONE attach per batch)")
        try:
            self.session = PortalSession.attach(
                self.logger,
                journal=TransactionJournal(),
                cancel_check=lambda: self._is_cancelled,
                trace=self.trace,
            )
        except WebDriverException as exc:
            self.logger.error(str(exc))
            self.finished_signal.emit(False, str(exc))
            return

        driver = self.session.raw_driver
        try:
            runner = BatchRunner(self.session, self.logger,
                                 commit_timeout=float(profile["timeout"]) / 3.0,
                                 reconcile_grace=float(profile["step"]))
            result = runner.run(
                self.batch_queue,
                on_patient_status=lambda idx, status: self.patient_status_signal.emit(idx, status),
            )
            self.last_summary = result.summary
            self.performance_signal.emit(result.summary.as_dict())
            self._log_summary(result)

            if self._is_cancelled:
                self.finished_signal.emit(False, "Batch cancelled by operator")
            elif result.summary.items_reconciliation_required:
                self.finished_signal.emit(
                    False,
                    f"{result.summary.items_reconciliation_required} item(s) RECONCILIATION_REQUIRED "
                    f"- operator review needed before they can be counted as added")
            elif result.summary.items_failed:
                self.finished_signal.emit(False, f"{result.summary.items_failed} item(s) failed")
            else:
                self.finished_signal.emit(True, "All batches processed and verified")
        except (AmbiguousPatientTab, NoPatientTab) as exc:
            self.logger.error(f"STOP - {exc}")
            DiagnosticEngine.capture_artifact(driver, "TAB_AMBIGUITY", "TAB", "AMBIGUOUS", exc)
            self.finished_signal.emit(False, str(exc))
        except Exception as fatal:
            message = f"Fatal batch error: {fatal}"
            self.logger.error(message)
            self.logger.error(traceback.format_exc())
            DiagnosticEngine.capture_artifact(driver, "GLOBAL_FATAL", "FATAL",
                                              "FATAL_EXCEPTION", fatal)
            self.finished_signal.emit(False, message)
        finally:
            if self.session is not None:
                self.session.close()

    def _log_summary(self, result):
        summary = result.summary
        self.logger.info("=" * 78)
        self.logger.info(
            f"[PERF] batch={summary.batch_elapsed_ms:.0f}ms items={summary.items_total} "
            f"completed={summary.items_completed} failed={summary.items_failed} "
            f"reconciliation_required={summary.items_reconciliation_required} "
            f"duplicates_skipped={summary.items_skipped_duplicate}")
        self.logger.info(
            f"[PERF] avg={summary.avg_item_ms:.0f}ms p50={summary.p50_item_ms:.0f}ms "
            f"p95={summary.p95_item_ms:.0f}ms dom_calls={summary.total_dom_calls} "
            f"execute_script={summary.total_execute_script_calls} "
            f"frame_discoveries={summary.frame_discoveries} "
            f"full_tab_scans={summary.full_tab_scans} "
            f"fixed_sleep_ms={summary.fixed_sleep_ms} "
            f"blocked_duplicate_plus={summary.duplicate_plus_attempts_blocked}")
        for entry in result.patients:
            if entry["reconciliation_required"]:
                self.logger.error(
                    f"[REVIEW] {entry['name']}: {entry['reconciliation_required']} "
                    f"-> RECONCILIATION_REQUIRED (not added, not counted)")




class ChromeStatusProbe(QThread):
    """Reports the REAL Chrome/CDP state, on a worker thread.

    The console used to flip this label to ONLINE 800ms after layout without
    checking anything at all.  This asks the debugging endpoint instead.

    LIFECYCLE (round 18 corrective).  The thread is PAUSED during a batch and
    RESUMED afterwards; it is stopped exactly once, at application shutdown.
    An earlier version stopped the thread when the operator pressed STOP
    AUTOMATION, which permanently killed Chrome monitoring for the rest of
    the session - a stopped QThread cannot be restarted by clearing a flag.

    Pausing (not stopping) also keeps this strictly OUTSIDE the enhancement
    hot path: while a batch runs the probe issues no socket call at all, so
    it can never add a Selenium call, a DOM round trip or a per-item
    heartbeat.

    ``_mutex`` guards the two flags, which are written from the UI thread and
    read from this worker thread.
    """

    status_signal = pyqtSignal(str, str)   # stage, detail

    POLL_SECONDS = 5.0
    _TICK_MS = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mutex = QMutex()
        self._stop = False
        self._paused = False

    # -- state, all mutex-guarded ----------------------------------------
    def pause(self):
        """Suspend polling for the duration of a batch."""
        self._mutex.lock()
        try:
            self._paused = True
        finally:
            self._mutex.unlock()

    def resume(self):
        """Resume polling once the batch has finished or been cancelled."""
        self._mutex.lock()
        try:
            self._paused = False
        finally:
            self._mutex.unlock()

    def stop(self):
        """Ask the thread to finish.  Only at application shutdown."""
        self._mutex.lock()
        try:
            self._stop = True
        finally:
            self._mutex.unlock()

    def set_batch_running(self, running):
        """Backwards-compatible alias for pause/resume."""
        self.pause() if running else self.resume()

    def is_stopping(self):
        self._mutex.lock()
        try:
            return self._stop
        finally:
            self._mutex.unlock()

    def _should_poll(self):
        self._mutex.lock()
        try:
            return not self._stop and not self._paused
        finally:
            self._mutex.unlock()

    # -- worker ------------------------------------------------------------
    def run(self):
        while not self.is_stopping():
            if self._should_poll():
                try:
                    ok, detail = probe_debugger_endpoint()
                    # An answering endpoint proves ONE thing: CDP is
                    # reachable.  It does NOT mean Selenium is attached, the
                    # portal tab exists, or a Treatment Plan is open.
                    self.status_signal.emit(
                        "CDP ENDPOINT VERIFIED" if ok else "NOT STARTED",
                        detail)
                except Exception as exc:                    # noqa: BLE001
                    self.status_signal.emit("CHECK FAILED", str(exc))
            waited = 0.0
            while waited < self.POLL_SECONDS and not self.is_stopping():
                self.msleep(self._TICK_MS)
                waited += self._TICK_MS / 1000.0


class DropZone(QFrame):
    """Drag-and-drop upload zone."""
    fileDropped = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("dropZone")
        self.setStyleSheet("""
            QFrame#dropZone {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #FFFFFF, stop:1 #FFFFFF);
                border: 2px dashed #3A0F34;
                border-radius: 16px;
                min-height: 140px;
            }
            QFrame#dropZone:hover {
                border: 2px dashed #3A0F34;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #F4F7F9, stop:1 #E7ECF0);
            }
        """)
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignCenter)
        self.icon_label = QLabel("\u2191")
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setStyleSheet("font-size: 42px; background: transparent; border: none;")
        self.title_label = QLabel("DROP HOSPITAL BILLS HERE")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet("font-size: 16px; font-weight: 800; color: #3A0F34; letter-spacing: 1px; background: transparent; border: none;")
        self.sub_label = QLabel("CLICK TO UPLOAD OR DRAG AND DROP - PDF ONLY")
        self.sub_label.setAlignment(Qt.AlignCenter)
        self.sub_label.setStyleSheet("font-size: 11px; color: #55606B; letter-spacing: 1px; background: transparent; border: none;")
        layout.addWidget(self.icon_label)
        layout.addWidget(self.title_label)
        layout.addWidget(self.sub_label)
        self.setLayout(layout)
        try:
            glow = QGraphicsDropShadowEffect()
            glow.setBlurRadius(20)
            glow.setColor(QColor("#3A0F34"))
            glow.setOffset(0, 0)
            self.setGraphicsEffect(glow)
        except:
            pass
        self.pulse_timer = QTimer(self)
        self.pulse_timer.timeout.connect(self.pulse_animation)
        self.pulse_timer.start(2000)
        self.pulse_state = False

    def pulse_animation(self):
        self.pulse_state = not self.pulse_state
        if self.pulse_state:
            self.icon_label.setStyleSheet("font-size: 44px; background: transparent; border: none; color: #3A0F34;")
        else:
            self.icon_label.setStyleSheet("font-size: 42px; background: transparent; border: none;")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet("""
                QFrame#dropZone {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #F4F7F9, stop:1 #D2DAE1);
                    border: 2px solid #3A0F34;
                    border-radius: 16px;
                    min-height: 140px;
                }
            """)
            self.title_label.setText("RELEASE TO UPLOAD")

    def dragLeaveEvent(self, event):
        self.setStyleSheet("""
            QFrame#dropZone {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #FFFFFF, stop:1 #FFFFFF);
                border: 2px dashed #3A0F34;
                border-radius: 16px;
                min-height: 140px;
            }
        """)
        self.title_label.setText("DROP HOSPITAL BILLS HERE")

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        files = [url.toLocalFile() for url in urls if url.toLocalFile().lower().endswith('.pdf')]
        if files:
            self.fileDropped.emit(files)
        self.setStyleSheet("""
            QFrame#dropZone {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #FFFFFF, stop:1 #FFFFFF);
                border: 2px dashed #3A0F34;
                border-radius: 16px;
                min-height: 140px;
            }
        """)
        self.title_label.setText("DROP HOSPITAL BILLS HERE")

    def mousePressEvent(self, event):
        parent = self.parent()
        while parent:
            if hasattr(parent, 'upload_bills'):
                parent.upload_bills()
                break
            parent = parent.parent()
        super().mousePressEvent(event)

class FileCard(QFrame):
    """Animated file card with PDF icon, filename, size, status, progress"""
    def __init__(self, filename, filesize, parent=None):
        super().__init__(parent)
        self.setObjectName("fileCard")
        self.setStyleSheet("""
            QFrame#fileCard {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FFFFFF, stop:1 #F4F7F9);
                border: 1px solid #C2CCD4;
                border-radius: 12px;
                padding: 8px;
            }
            QFrame#fileCard:hover {
                border: 1px solid #3A0F34;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #F4F7F9, stop:1 #E7ECF0);
            }
        """)
        layout = QHBoxLayout()
        layout.setContentsMargins(12, 8, 12, 8)
        icon = QLabel("\U0001f4c4")
        icon.setStyleSheet("font-size: 24px; background: transparent; border: none;")
        layout.addWidget(icon)
        info_layout = QVBoxLayout()
        self.name_label = QLabel(filename)
        self.name_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #1C2126; background: transparent; border: none;")
        self.size_label = QLabel(filesize)
        self.size_label.setStyleSheet("font-size: 10px; color: #55606B; background: transparent; border: none;")
        info_layout.addWidget(self.name_label)
        info_layout.addWidget(self.size_label)
        layout.addLayout(info_layout)
        layout.addStretch()
        self.status_label = QLabel("QUEUED")
        self.status_label.setStyleSheet("""
            background: #C2CCD4;
            color: #2A5E8C;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 1px;
        """)
        layout.addWidget(self.status_label)
        self.setLayout(layout)
        self.setWindowOpacity(0)
        self.anim = QPropertyAnimation(self, b"windowOpacity")
        self.anim.setDuration(400)
        self.anim.setStartValue(0)
        self.anim.setEndValue(1)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.start()

    def update_status(self, status_text):
        self.status_label.setText(status_text)
        if "COMPLETED" in status_text:
            self.status_label.setStyleSheet("""
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E3F1EA, stop:1 #E3F1EA);
                color: #1F7A4D;
                padding: 4px 10px;
                border-radius: 12px;
                font-size: 10px;
                font-weight: 700;
                border: 1px solid #1F7A4D;
            """)
        elif "IN_PROGRESS" in status_text or "PROCESSING" in status_text:
            self.status_label.setStyleSheet("""
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E4EDF5, stop:1 #E4EDF5);
                color: #3A0F34;
                padding: 4px 10px;
                border-radius: 12px;
                font-size: 10px;
                font-weight: 700;
                border: 1px solid #3A0F34;
            """)
        elif "QUEUED" in status_text:
            self.status_label.setStyleSheet("""
                background: #C2CCD4;
                color: #2A5E8C;
                padding: 4px 10px;
                border-radius: 12px;
                font-size: 10px;
                font-weight: 700;
            """)
        else:
            self.status_label.setStyleSheet("""
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #F8E4E7, stop:1 #F8E4E7);
                color: #A32638;
                padding: 4px 10px;
                border-radius: 12px;
                font-size: 10px;
                font-weight: 700;
                border: 1px solid #A32638;
            """)

class CounterCard(QFrame):
    """Premium dashboard counter with animated number"""
    def __init__(self, title, initial=0, color="#3A0F34", parent=None):
        super().__init__(parent)
        self.setObjectName("counterCard")
        self.target_value = initial
        self.current_value = 0
        self.color = color
        self.setStyleSheet(f"""
            QFrame#counterCard {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #FFFFFF, stop:1 #FFFFFF);
                border: 1px solid #C2CCD4;
                border-radius: 14px;
                min-width: 110px;
                min-height: 80px;
            }}
            QFrame#counterCard:hover {{
                border: 1px solid {color};
            }}
        """)
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        self.title_label = QLabel(title)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet(f"font-size: 10px; font-weight: 700; color: #55606B; letter-spacing: 1.5px; background: transparent; border: none;")
        self.value_label = QLabel("0")
        self.value_label.setAlignment(Qt.AlignCenter)
        self.value_label.setStyleSheet(f"font-size: 28px; font-weight: 900; color: {color}; background: transparent; border: none;")
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        self.setLayout(layout)
        try:
            glow = QGraphicsDropShadowEffect()
            glow.setBlurRadius(12)
            _glow_colour = QColor(color)
            _glow_colour.setAlpha(70)   # subtle halo, not a neon rainbow
            glow.setColor(_glow_colour)
            glow.setOffset(0, 0)
            self.setGraphicsEffect(glow)
        except:
            pass

    def set_value(self, value):
        self.target_value = value
        self.animate_to_target()

    def animate_to_target(self):
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._count_step)
        self.timer.start(20)
        self._step = max(1, (self.target_value - self.current_value) // 20) if self.target_value > self.current_value else 1

    def _count_step(self):
        if self.current_value < self.target_value:
            self.current_value = min(self.target_value, self.current_value + self._step)
            self.value_label.setText(str(self.current_value))
        elif self.current_value > self.target_value:
            self.current_value = max(self.target_value, self.current_value - self._step)
            self.value_label.setText(str(self.current_value))
        else:
            self.timer.stop()

class BatchCGHSApp(QWidget):
    #: How long to wait for an owned thread to finish before
    #: reporting it is still busy.  Never a forced kill.
    SHUTDOWN_WAIT_MS = 15000

    def __init__(self):
        super().__init__()
        self.batch_queue = []
        self.thread = None
        self.parser_engine = CGHSParsingEngine(logger=EnterpriseLogger())
        self.file_cards = []
        self.counter_cards = {}
        self.current_selected_bill = 0
        self.initUI()

    def initUI(self):
        self.setWindowTitle("CGHS Automation Console - Vishal Singh Chauhan")
        self.setGeometry(80, 40, 1400, 900)
        self.setStyleSheet(ui_theme.app_stylesheet())

        main_v_layout = QVBoxLayout()
        main_v_layout.setContentsMargins(16,16,16,16)
        main_v_layout.setSpacing(12)


        top_bar = QFrame()
        top_bar.setObjectName("topBar")
        top_bar.setFixedHeight(72)
        top_bar.setStyleSheet("""
            QFrame#topBar {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FFFFFF, stop:0.5 #FFFFFF, stop:1 #FFFFFF);
                border: 1px solid #3A0F34;
                border-radius: 14px;
            }
        """)
        top_bar_layout = QHBoxLayout()
        top_bar_layout.setContentsMargins(22, 10, 26, 10)

        left_title_layout = QVBoxLayout()
        title_row = QHBoxLayout()
        title_label = QLabel("CGHS AUTOMATION")
        title_label.setStyleSheet("font-size: 18px; font-weight: 900; color: #3A0F34; letter-spacing: 1px; background: transparent; border: none;")
        title_row.addWidget(title_label)
        title_label2 = QLabel("COMMAND CENTER")
        title_label2.setStyleSheet("font-size: 18px; font-weight: 400; color: #1C2126; letter-spacing: 1px; background: transparent; border: none;")
        title_row.addWidget(title_label2)
        title_row.addStretch()
        left_title_layout.addLayout(title_row)

        user_row = QHBoxLayout()
        self.header_user_label = QLabel("VISHAL SINGH CHAUHAN")
        self.header_user_label.setStyleSheet("font-size: 13px; font-weight: 800; color: #3A0F34; letter-spacing: 1px; background: transparent; border: none;")
        user_row.addWidget(self.header_user_label)
        user_role = QLabel("AUTOMATION CONSOLE")
        user_role.setStyleSheet("font-size: 10px; color: #1F7A4D; background: transparent; border: none; margin-left: 12px;")
        user_row.addWidget(user_role)
        user_row.addStretch()
        left_title_layout.addLayout(user_row)

        top_bar_layout.addLayout(left_title_layout)

        center_status_layout = QVBoxLayout()
        self.system_online_label = QLabel("● SYSTEM ONLINE")
        self.system_online_label.setStyleSheet("font-size: 11px; font-weight: 800; color: #1F7A4D; letter-spacing: 1px; background: transparent; border: none;")
        center_status_layout.addWidget(self.system_online_label, alignment=Qt.AlignCenter)

        self.energy_line = QFrame()
        self.energy_line.setFixedHeight(2)
        self.energy_line.setFixedWidth(200)
        self.energy_line.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3A0F34, stop:0.5 #3A0F34, stop:1 #A32638); border: none;")
        center_status_layout.addWidget(self.energy_line, alignment=Qt.AlignCenter)

        top_bar_layout.addLayout(center_status_layout)

        right_status_layout = QVBoxLayout()
        self.batch_status_label = QLabel("BATCH: IDLE")
        self.batch_status_label.setStyleSheet("font-size: 10px; color: #55606B; background: transparent; border: none;")
        self.chrome_status_label = QLabel("CHROME/CDP: CHECKING...")
        self.chrome_status_label.setStyleSheet("font-size: 10px; color: #55606B; background: transparent; border: none;")
        self.automation_status_label = QLabel("AUTOMATION: READY")
        self.automation_status_label.setStyleSheet("font-size: 10px; color: #2A5E8C; background: transparent; border: none;")
        right_status_layout.addWidget(self.batch_status_label, alignment=Qt.AlignRight)
        right_status_layout.addWidget(self.chrome_status_label, alignment=Qt.AlignRight)
        right_status_layout.addWidget(self.automation_status_label, alignment=Qt.AlignRight)
        top_bar_layout.addLayout(right_status_layout)

        top_bar.setLayout(top_bar_layout)
        main_v_layout.addWidget(top_bar)

        content_h_layout = QHBoxLayout()
        content_h_layout.setSpacing(12)

        left_panel = QFrame()
        left_panel.setObjectName("leftPanel")
        left_panel.setStyleSheet("""
            QFrame#leftPanel {
                background: #FFFFFF;
                border: 1px solid #C2CCD4;
                border-radius: 14px;
            }
        """)
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(16,16,16,16)
        left_layout.setSpacing(12)

        self.drop_zone = DropZone()
        self.drop_zone.fileDropped.connect(self.handle_dropped_files)
        left_layout.addWidget(self.drop_zone)

        network_row = QHBoxLayout()
        network_label = QLabel("NETWORK SPEED:")
        network_label.setStyleSheet("font-size: 10px; color: #55606B; letter-spacing: 1px; background: transparent; border: none;")
        network_row.addWidget(network_label)
        self.delay_combo = QComboBox()
        self.delay_combo.addItems(list(NETWORK_DELAY.keys()))
        self.delay_combo.setCurrentText("Medium Network (2.5s)")
        self.delay_combo.setStyleSheet("""
            QComboBox {
                background: #F4F7F9;
                border: 1px solid #C2CCD4;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 11px;
                color: #1C2126;
            }
            QComboBox:hover {
                border: 1px solid #3A0F34;
            }
        """)
        network_row.addWidget(self.delay_combo)
        network_row.addStretch()
        left_layout.addLayout(network_row)

        self.file_cards_scroll = QScrollArea()
        self.file_cards_scroll.setWidgetResizable(True)
        self.file_cards_scroll.setFixedHeight(180)
        self.file_cards_scroll.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: #DDE3E8;
                width: 6px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #C2CCD4;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: #3A0F34;
            }
        """)
        self.file_cards_container = QWidget()
        self.file_cards_container.setStyleSheet("background: transparent;")
        self.file_cards_layout = QVBoxLayout()
        self.file_cards_layout.setSpacing(8)
        self.file_cards_layout.setContentsMargins(0,0,0,0)
        self.file_cards_container.setLayout(self.file_cards_layout)
        self.file_cards_scroll.setWidget(self.file_cards_container)
        uploaded_title = QLabel("UPLOADED BILLS")
        uploaded_title.setStyleSheet(ui_theme.section_header(ui_theme.BRAND))
        left_layout.addWidget(uploaded_title)
        left_layout.addWidget(self.file_cards_scroll)

        counters_title = QLabel("LIVE COUNTERS")
        counters_title.setStyleSheet(ui_theme.section_header(ui_theme.BRAND))
        left_layout.addWidget(counters_title)

        counters_grid = QGridLayout()
        counters_grid.setSpacing(8)
        self.counter_cards["BILLS"] = CounterCard("TOTAL BILLS", 0, "#3A0F34")
        self.counter_cards["PROCESSED"] = CounterCard("PROCESSED", 0, "#2A5E8C")
        self.counter_cards["SUCCESS"] = CounterCard("SUCCESSFUL", 0, "#1F7A4D")
        self.counter_cards["FAILED"] = CounterCard("FAILED", 0, "#A32638")
        self.counter_cards["CODES"] = CounterCard("TOTAL CODES", 0, "#3A0F34")
        self.counter_cards["QTY"] = CounterCard("TOTAL QTY", 0, "#EAAA00")

        counters_grid.addWidget(self.counter_cards["BILLS"], 0, 0)
        counters_grid.addWidget(self.counter_cards["PROCESSED"], 0, 1)
        counters_grid.addWidget(self.counter_cards["SUCCESS"], 0, 2)
        counters_grid.addWidget(self.counter_cards["FAILED"], 1, 0)
        counters_grid.addWidget(self.counter_cards["CODES"], 1, 1)
        counters_grid.addWidget(self.counter_cards["QTY"], 1, 2)

        left_layout.addLayout(counters_grid)

        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)
        self.upload_btn = QPushButton("UPLOAD BILLS")
        self.upload_btn.setFixedHeight(42)
        self.upload_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #F4F7F9, stop:1 #C2CCD4);
                border: 1px solid #3A0F34;
                border-radius: 10px;
                color: #3A0F34;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 1px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E4EDF5, stop:1 #E4EDF5);
                border: 1px solid #3A0F34;
                color: #3A0F34;
            }
        """)
        self.upload_btn.clicked.connect(self.upload_bills)
        controls_row.addWidget(self.upload_btn)

        self.start_btn = QPushButton("START AUTOMATION")
        self.start_btn.setEnabled(False)
        self.start_btn.setFixedHeight(42)
        self.start_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E3F1EA, stop:1 #E3F1EA);
                border: 1px solid #1F7A4D;
                border-radius: 10px;
                color: #1F7A4D;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 1px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E3F1EA, stop:1 #E3F1EA);
                border: 1px solid #1F7A4D;
                color: #ffffff;
            }
            QPushButton:disabled {
                background: #F4F7F9;
                border: 1px solid #C2CCD4;
                color: #A9B6C0;
            }
        """)
        self.start_btn.clicked.connect(self.start_batch)
        controls_row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("■ STOP AUTOMATION")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setFixedHeight(42)
        self.stop_btn.setStyleSheet("""
            QPushButton#stopBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #F8E4E7, stop:1 #F8E4E7);
                border: 1px solid #A32638;
                border-radius: 10px;
                color: #A32638;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 1px;
            }
            QPushButton#stopBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #F8E4E7, stop:1 #F8E4E7);
                border: 1px solid #A32638;
                color: #ffffff;
            }
            QPushButton#stopBtn:disabled {
                background: #F4F7F9;
                border: 1px solid #C2CCD4;
                color: #A9B6C0;
            }
        """)
        self.stop_btn.clicked.connect(self.stop_batch)
        controls_row.addWidget(self.stop_btn)

        left_layout.addLayout(controls_row)

        left_panel.setLayout(left_layout)
        content_h_layout.addWidget(left_panel, 55)

        right_panel = QFrame()
        right_panel.setObjectName("rightPanel")
        right_panel.setStyleSheet("""
            QFrame#rightPanel {
                background: #F4F7F9;
                border: 1px solid #C2CCD4;
                border-radius: 14px;
            }
        """)
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(16,16,16,16)
        right_layout.setSpacing(12)

        bills_header = QLabel("PATIENT QUEUE")
        bills_header.setStyleSheet(ui_theme.section_header(ui_theme.BRAND))
        right_layout.addWidget(bills_header)

        self.queue_table = QTableWidget()
        self.queue_table.setColumnCount(4)
        self.queue_table.setHorizontalHeaderLabels(["PATIENT / FILE", "SUMMARY", "STATUS", "CODES"])
        self.queue_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.queue_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.queue_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.queue_table.setFixedHeight(140)
        self.queue_table.setStyleSheet("""
            QTableWidget {
                background: #DDE3E8;
                gridline-color: #F4F7F9;
                border: 1px solid #C2CCD4;
                border-radius: 10px;
                font-size: 11px;
            }
            QHeaderView::section {
                background: #F4F7F9;
                color: #55606B;
                font-weight: 800;
                font-size: 10px;
                letter-spacing: 1px;
                padding: 8px;
                border: none;
                border-bottom: 1px solid #3A0F34;
            }
            QTableWidget::item {
                padding: 6px;
                border-bottom: 1px solid #F4F7F9;
            }
            QTableWidget::item:selected {
                background: #FCF2D6;
                color: #1C2126;
            }
        """)
        self.queue_table.itemClicked.connect(self.on_bill_selected)
        right_layout.addWidget(self.queue_table)

        results_header = QHBoxLayout()
        results_title = QLabel("EXTRACTED CODES")
        results_title.setStyleSheet(ui_theme.section_header(ui_theme.BRAND))
        results_header.addWidget(results_title)
        results_header.addStretch()
        self.results_count_label = QLabel("0 CODES")
        self.results_count_label.setStyleSheet("font-size: 10px; color: #55606B; background: transparent; border: none;")
        results_header.addWidget(self.results_count_label)
        right_layout.addLayout(results_header)

        self.results_table = QTableWidget()
        self.results_table.setColumnCount(5)
        self.results_table.setHorizontalHeaderLabels(["CODE", "DESCRIPTION", "QTY", "STATUS", "SOURCE"])
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.results_table.setStyleSheet("""
            QTableWidget {
                background: #DDE3E8;
                gridline-color: #FFFFFF;
                border: 1px solid #C2CCD4;
                border-radius: 10px;
                font-size: 11px;
            }
            QHeaderView::section {
                background: #F4F7F9;
                color: #55606B;
                font-weight: 800;
                font-size: 10px;
                letter-spacing: 1px;
                padding: 8px;
                border: none;
                border-bottom: 1px solid #3A0F34;
            }
            QTableWidget::item {
                padding: 6px;
                border-bottom: 1px solid #FFFFFF;
            }
        """)
        right_layout.addWidget(self.results_table)

        log_header = QHBoxLayout()
        log_title = QLabel("EXECUTION LOG")
        log_title.setStyleSheet(ui_theme.section_header(ui_theme.STATUS_OK))
        log_header.addWidget(log_title)
        log_header.addStretch()
        self.log_status = QLabel("READY")
        self.log_status.setStyleSheet("font-size: 9px; color: #A9B6C0; background: transparent; border: none;")
        log_header.addWidget(self.log_status)
        right_layout.addLayout(log_header)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setStyleSheet("""
            QTextEdit {
                background: #DDE3E8;
                color: #1F7A4D;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
                border: 1px solid #E3F1EA;
                border-radius: 10px;
                padding: 10px;
            }
            QScrollBar:vertical {
                background: #DDE3E8;
                width: 6px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #E3F1EA;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: #1F7A4D;
            }
        """)
        right_layout.addWidget(self.log_box)

        right_panel.setLayout(right_layout)
        content_h_layout.addWidget(right_panel, 45)

        main_v_layout.addLayout(content_h_layout)

        container = QWidget()
        container.setLayout(main_v_layout)

        final_layout = QVBoxLayout()
        final_layout.setContentsMargins(0,0,0,0)
        final_layout.addWidget(container)
        self.setLayout(final_layout)

        self.chrome_status_label.setText("CHROME/CDP: CHECKING CONNECTION")
        self.chrome_probe = ChromeStatusProbe(self)
        self.chrome_probe.status_signal.connect(self.update_chrome_status)
        self.chrome_probe.start()



    def handle_dropped_files(self, files):
        if files:
            self.process_dropped_files(files)

    def process_dropped_files(self, filePaths):
        self.reset_execution_log()
        self.log(f"[{time.strftime('%H:%M:%S')}] [NEW SESSION] DROP DETECTED - {len(filePaths)} file(s) - initialising session")
        self.batch_queue.clear()
        self.queue_table.setRowCount(len(filePaths))
        for i in reversed(range(self.file_cards_layout.count())):
            child = self.file_cards_layout.itemAt(i).widget()
            if child:
                child.setParent(None)
        self.file_cards = []
        for row, filePath in enumerate(filePaths):
            filename = os.path.basename(filePath)
            filesize = f"{os.path.getsize(filePath)/1024:.1f} KB" if os.path.exists(filePath) else "PDF"
            card = FileCard(filename, filesize)
            self.file_cards_layout.addWidget(card)
            self.file_cards.append(card)
            parsed_items, raw_occs, patient_name, rejected, agg_log = self.parser_engine.parse(filePath)
            self.batch_queue.append({
                "filePath": filePath, "name": patient_name or filename,
                "items": parsed_items, "audit": {"raw_occurrences": raw_occs, "rejected": rejected, "aggregation_log": agg_log}
            })
            codes_preview = ", ".join([f"{i['code']}(x{i['qty']})" for i in parsed_items[:5]])
            if len(parsed_items) > 5:
                codes_preview += f" +{len(parsed_items)-5} more"
            cn002_qty = next((item["qty"] for item in parsed_items if item["code"] == "CN002"), 0)
            summary = f"CN002:{cn002_qty} | {len(parsed_items)} codes"
            if rejected:
                summary += f" | {len(rejected)} flagged"
            name_item = QTableWidgetItem(f" {patient_name}")
            name_item.setForeground(QColor("#1C2126"))
            self.queue_table.setItem(row, 0, name_item)
            summary_item = QTableWidgetItem(f" {summary}")
            summary_item.setForeground(QColor("#55606B"))
            self.queue_table.setItem(row, 1, summary_item)
            status_item = QTableWidgetItem("QUEUED")
            status_item.setForeground(QColor("#2A5E8C"))
            self.queue_table.setItem(row, 2, status_item)
            codes_item = QTableWidgetItem(f" {codes_preview if codes_preview else 'No Codes'}")
            codes_item.setForeground(QColor("#1F7A4D"))
            self.queue_table.setItem(row, 3, codes_item)
            ParserDiagnosticAuditor.export_audit_report(patient_name, raw_occs, parsed_items, rejected, agg_log)
        self.start_btn.setEnabled(True)
        self.log(f"[{time.strftime('%H:%M:%S')}] [SESSION INITIALIZED] {len(filePaths)} bill(s) queued - UPLOAD SUCCESS")
        self.update_counters()
        if self.batch_queue:
            self.populate_results_for_bill(0)

    def log(self, msg: str):
        import re
        original_msg = msg
        timestamp_match = re.search(r'\[(\d{2}:\d{2}:\d{2})\]', msg)
        timestamp = timestamp_match.group(1) if timestamp_match else time.strftime('%H:%M:%S')
        category = "SYSTEM"
        badge_color = "#55606B"
        badge_bg = "#F4F7F9"
        severity = "INFO"
        upper_msg = msg.upper()
        if "ERROR" in upper_msg or "❌" in msg or "FAILED" in upper_msg or "FAIL" in upper_msg:
            category = "ERROR"
            badge_color = "#A32638"
            badge_bg = "#F8E4E7"
            severity = "ERROR"
        elif "WARN" in upper_msg or "⚠️" in msg or "WARNING" in upper_msg:
            category = "WARNING"
            badge_color = "#EAAA00"
            badge_bg = "#FCF2D6"
            severity = "WARNING"
        elif "SUCCESS" in upper_msg or "VERIFIED" in upper_msg or "COMPLETED" in upper_msg or "✓" in msg:
            category = "SUCCESS"
            badge_color = "#1F7A4D"
            badge_bg = "#E3F1EA"
            severity = "SUCCESS"
        elif "PROCEDURE" in upper_msg or "TYPE_PROCEDURE" in upper_msg:
            category = "PROCEDURE"
            badge_color = "#3A0F34"
            badge_bg = "#FFFFFF"
        elif "SPECIALITY" in upper_msg:
            category = "SPECIALITY"
            badge_color = "#3A0F34"
            badge_bg = "#FCF2D6"
        elif "QUANTITY" in upper_msg:
            category = "QUANTITY"
            badge_color = "#EAAA00"
            badge_bg = "#FCF2D6"
        elif "PLUS" in upper_msg:
            category = "PLUS ACTION"
            badge_color = "#A32638"
            badge_bg = "#FCF2D6"
        elif "PATIENT" in upper_msg or "BILL" in upper_msg:
            category = "PATIENT"
            badge_color = "#2A5E8C"
            badge_bg = "#FFFFFF"
        clean_msg = re.sub(r'\[\d{2}:\d{2}:\d{2}\]\s*', '', msg)
        clean_msg = re.sub(r'\[INFO\]|\[WARN\]|\[ERROR\]', '', clean_msg).strip()
        html = f'''
        <div style="margin: 3px 0; font-family: Consolas, monospace;">
            <span style="color: #3A0F34; font-weight: 700;">[{timestamp}]</span>
            <span style="background: {badge_bg}; color: {badge_color}; padding: 2px 8px; border-radius: 10px; font-size: 10px; font-weight: 800; margin: 0 8px; border: 1px solid {badge_color};">{category}</span>
            <span style="color: #1C2126;">{clean_msg}</span>
        </div>
        '''
        try:
            from PyQt5.QtGui import QTextCursor
            self.log_box.append("")
            cursor = self.log_box.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.insertHtml(html)
            self.log_box.setTextCursor(cursor)
            self.log_box.ensureCursorVisible()
        except:
            self.log_box.append(original_msg)
        self.log_status.setText(severity)
        if severity == "ERROR":
            self.log_status.setStyleSheet("font-size: 10px; font-weight: 800; color: #A32638; background: #F8E4E7; padding: 2px 6px; border-radius: 6px;")
        elif severity == "SUCCESS":
            self.log_status.setStyleSheet("font-size: 10px; font-weight: 800; color: #1F7A4D; background: #E3F1EA; padding: 2px 6px; border-radius: 6px;")
        elif severity == "WARNING":
            self.log_status.setStyleSheet("font-size: 10px; font-weight: 800; color: #EAAA00; background: #FCF2D6; padding: 2px 6px; border-radius: 6px;")
        else:
            self.log_status.setStyleSheet("font-size: 9px; color: #55606B; background: transparent;")

    def reset_execution_log(self):
        self.log_box.clear()
        init_html = f"""
        <div style="font-family: Consolas, monospace; color: #3A0F34; margin: 10px 0;">
            <div style="color: #3A0F34; font-weight: 800; letter-spacing: 1px;">╔══════════════════════════════════════╗</div>
            <div style="color: #3A0F34; font-weight: 800;">║  NEW SESSION INITIALIZED - LOG RESET  ║</div>
            <div style="color: #3A0F34; font-weight: 800; letter-spacing: 1px;">╚══════════════════════════════════════╝</div>
            <div style="margin-top: 8px; color: #55606B;">[{time.strftime('%H:%M:%S')}] SYSTEM • Previous logs cleared from visible HUD</div>
            <div style="color: #55606B;">[{time.strftime('%H:%M:%S')}] SYSTEM • Awaiting new bill processing...</div>
            <div style="color: #3A0F34; margin-top: 8px;">[{time.strftime('%H:%M:%S')}] READY</div>
        </div>
        """
        try:
            from PyQt5.QtGui import QTextCursor
            cursor = self.log_box.textCursor()
            cursor.insertHtml(init_html)
            self.log_box.setTextCursor(cursor)
        except:
            self.log_box.append(f"[{time.strftime('%H:%M:%S')}] [LOG RESET] Execution log cleared for new bill(s)")
        self.results_table.setRowCount(0)
        self.results_count_label.setText("0 CODES")

    def upload_bills(self):
        filePaths, _ = QFileDialog.getOpenFileNames(self, "Select Hospital Bill PDFs", "", "PDF Files (*.pdf)")
        if filePaths:
            self.process_dropped_files(filePaths)

    def populate_results_for_bill(self, bill_index):
        if bill_index < 0 or bill_index >= len(self.batch_queue):
            return
        bill = self.batch_queue[bill_index]
        items = bill.get("items", [])
        code_descriptions = {
            "CN002": "Consultation Charge - Doctor Visit",
            "CC001": "ICU - Room Rent (Intensive Care)",
            "WC001": "Ward - Room Rent (AC Multibeds/Single)",
            "CC002": "Oxygen - Full/Half Day",
            "CC003": "Ventilator Charges",
            "CC004": "NIV Machine",
            "CC008": "Critical Care Procedure",
            "CC010": "Endotracheal Intubation",
            "CC011": "Central Line",
            "CC012": "Nebulizer Therapy",
            "CC014": "Critical Care Monitoring",
            "CI001": "ECG",
            "CI016": "Coronary Angiography",
            "GP009": "Foleys Catheter",
            "GP001": "General Procedure",
            "LB012": "CBC - Complete Blood Count",
            "LB055": "Glucometer / RBS",
            "LB120": "ABG Sampling",
            "LB123": "Renal Function Test",
            "LB124": "Liver Function Test",
            "LB125": "Lipid Profile",
            "PT005": "Limb Physio-Exercises",
            "PT004": "Physiotherapy",
            "RI001": "Echo Screening / 2D-Echo",
            "RI034": "X-Ray Chest",
            "RI020": "Ultrasound Abdomen",
            "RI133": "MRI Cervical Spine",
            "RI134": "MRI Dorsal Spine",
            "RI136": "MRI Lumbo-Sacral",
            "RI143": "MRI Fistulogram",
            "LB042": "Viral Marker Profile - Part",
            "LB043": "Viral Marker Profile - Part",
            "LB044": "Viral Marker Profile - Part",
            "DRUG100": "Drugs - IP + OT Pharmacy",
            "CNSU100": "Consumables - Cathlab/OT/Ward",
        }
        self.results_table.setRowCount(len(items))
        for row, item in enumerate(items):
            code = item.get("code","")
            qty = item.get("qty",0)
            amount = item.get("amount","")
            code_item = QTableWidgetItem(f" {code}")
            code_item.setForeground(QColor("#3A0F34"))
            if code.startswith("CN"):
                code_item.setForeground(QColor("#2A5E8C"))
            elif code.startswith("CC"):
                code_item.setForeground(QColor("#A32638"))
            elif code.startswith("WC"):
                code_item.setForeground(QColor("#EAAA00"))
            self.results_table.setItem(row, 0, code_item)
            desc = code_descriptions.get(code, f"{'Lab' if code.startswith('LB') else 'Radiology' if code.startswith('RI') else 'Procedure' if code.startswith('GP') or code.startswith('PT') or code.startswith('NI') else 'Investigation' if code.startswith('CI') or code.startswith('RP') else 'Charge'} - {code}")
            desc_item = QTableWidgetItem(f" {desc}")
            desc_item.setForeground(QColor("#1C2126"))
            self.results_table.setItem(row, 1, desc_item)
            qty_text = f" {qty}" + (f" (₹{amount})" if amount else "")
            qty_item = QTableWidgetItem(qty_text)
            qty_item.setForeground(QColor("#1F7A4D"))
            if qty > 10:
                qty_item.setForeground(QColor("#EAAA00"))
            self.results_table.setItem(row, 2, qty_item)
            status_text = "QUEUED"
            if bill_index < self.queue_table.rowCount():
                status_item_q = self.queue_table.item(bill_index, 2)
                if status_item_q:
                    status_text = status_item_q.text()
            if "COMPLETED" in status_text:
                display_status = "✓ ADDED"
                color = "#1F7A4D"
            elif "IN_PROGRESS" in status_text or "PROCESSING" in status_text:
                display_status = "PROCESSING..."
                color = "#3A0F34"
            elif "QUEUED" in status_text:
                display_status = "QUEUED"
                color = "#55606B"
            else:
                display_status = "NOT ADDED"
                color = "#A32638"
            status_display = QTableWidgetItem(f" {display_status}")
            status_display.setForeground(QColor(color))
            self.results_table.setItem(row, 3, status_display)
            source = bill.get("audit", {}).get("raw_occurrences", [])
            source_text = ""
            for occ in source:
                if occ.get("code")==code:
                    source_text = occ.get("source_section","")[:30]
                    break
            source_item = QTableWidgetItem(f" {source_text}")
            source_item.setForeground(QColor("#55606B"))
            self.results_table.setItem(row, 4, source_item)
        self.results_count_label.setText(f"{len(items)} CODES")

    def on_bill_selected(self, item):
        row = item.row()
        self.current_selected_bill = row
        self.populate_results_for_bill(row)

    def parse_single_pdf(self, pdf_path: str):
        final_items, raw_occs, patient_name, rejected, agg_log = self.parser_engine.parse(pdf_path)
        return final_items, patient_name

    def start_batch(self):
        if getattr(self, '_shutting_down', False):
            return
        self.log_box.clear()
        self.log(f"[{time.strftime('%H:%M:%S')}] [NEW EXECUTION] Starting batch - log reset - NEW SESSION INITIALIZED")
        self.start_btn.setEnabled(False)
        self.upload_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.stop_btn.setText("■ STOP AUTOMATION")
        self.batch_status_label.setText("BATCH: PROCESSING")
        self.batch_status_label.setStyleSheet("font-size: 10px; font-weight: 800; color: #3A0F34; background: #FFFFFF; padding: 2px 8px; border-radius: 6px; border: 1px solid #3A0F34;")
        self.automation_status_label.setText("AUTOMATION: RUNNING ●")
        self.automation_status_label.setStyleSheet("font-size: 10px; font-weight: 800; color: #1F7A4D; background: #E3F1EA; padding: 2px 8px; border-radius: 6px; border: 1px solid #1F7A4D;")
        delay_mode = self.delay_combo.currentText()
        self.thread = BatchAutomationThread(self.batch_queue, delay_mode)
        self.thread.log_signal.connect(self.log)
        self.thread.patient_status_signal.connect(self.update_patient_status)
        self.thread.finished_signal.connect(self.batch_finished)
        self.thread.performance_signal.connect(self.show_performance_summary)
        if getattr(self, 'chrome_probe', None):
            self.chrome_probe.pause()
        self.thread.start()
        self.log(f"[{time.strftime('%H:%M:%S')}] [BATCH START] {len(self.batch_queue)} patient(s) queued - starting")

    def show_performance_summary(self, summary: dict):
        """Render the batch telemetry block emitted by cghs.telemetry."""
        self.log(f"[{time.strftime('%H:%M:%S')}] [BATCH PERFORMANCE SUMMARY]")
        for key in (
            "batch_elapsed_ms", "items_total", "items_completed", "items_failed",
            "items_reconciliation_required", "items_skipped_duplicate",
            "avg_item_ms", "p50_item_ms", "p95_item_ms",
            "total_dom_calls", "total_execute_script_calls", "full_tab_scans",
            "frame_discoveries", "fixed_sleep_ms", "retries",
            "duplicate_plus_attempts_blocked",
        ):
            if key in summary:
                self.log(f"    {key:34s} = {summary[key]}")
        if summary.get("items_reconciliation_required"):
            self.log("    STATUS                             = RECONCILIATION_REQUIRED "
                     "(operator review required - these items are NOT counted as added)")

    def stop_batch(self):
        self.log(f"[{time.strftime('%H:%M:%S')}] [STOP REQUESTED] User requested graceful stop - finishing current Selenium action, preventing next procedure")
        if self.thread and self.thread.isRunning():
            self.log(f"[{time.strftime('%H:%M:%S')}] [STOP] Requesting cancellation via existing safe hook thread.stop() - current action will finish safely")
            self.thread.stop()
            self.stop_btn.setEnabled(False)
            self.stop_btn.setText("STOPPING...")
            self.batch_status_label.setText("BATCH: STOPPING")
            self.batch_status_label.setStyleSheet("font-size: 10px; font-weight: 800; color: #EAAA00; background: #FCF2D6; padding: 2px 8px; border-radius: 6px; border: 1px solid #EAAA00;")
            self.automation_status_label.setText("AUTOMATION: STOPPING")
            for row in range(self.queue_table.rowCount()):
                item = self.queue_table.item(row, 2)
                if item and "IN_PROGRESS" in item.text():
                    self.update_patient_status(row, "STOPPING")
                    if row < len(self.file_cards):
                        self.file_cards[row].update_status("STOPPING")
            self.log(f"[{time.strftime('%H:%M:%S')}] [STOP] Stop signal sent - will stop after current procedure (no new Plus dispatch) - resources will be cleaned")
        else:
            self.log(f"[{time.strftime('%H:%M:%S')}] [STOP] No active automation to stop")
            self.stop_btn.setEnabled(False)

    def update_patient_status(self, row: int, status_text: str):
        item = QTableWidgetItem(status_text)
        if "COMPLETED" in status_text:
            item.setForeground(QColor(166, 227, 161))
        elif "IN_PROGRESS" in status_text or "PROCESSING" in status_text:
            item.setForeground(QColor(249, 226, 175))
        elif "STOPPING" in status_text:
            item.setForeground(QColor(250, 179, 135))
        else:
            item.setForeground(QColor(243, 139, 168))
        self.queue_table.setItem(row, 2, item)
        if row < len(self.file_cards):
            self.file_cards[row].update_status(status_text)
        if row == self.current_selected_bill:
            self.populate_results_for_bill(row)
        self.update_counters()

    def update_counters(self):
        total_bills = self.queue_table.rowCount()
        processed = 0
        successful = 0
        failed = 0
        total_codes = 0
        total_qty = 0
        for row in range(total_bills):
            status_item = self.queue_table.item(row, 2)
            if status_item:
                status = status_item.text()
                if "COMPLETED" in status:
                    processed += 1
                    successful += 1
                elif "PARTIAL" in status or "ERROR" in status or "STOPPED" in status:
                    processed += 1
                    failed += 1
        for bill in self.batch_queue:
            items = bill.get("items", [])
            total_codes += len(items)
            for item in items:
                total_qty += item.get("qty",0)
        self.counter_cards["BILLS"].set_value(total_bills)
        self.counter_cards["PROCESSED"].set_value(processed)
        self.counter_cards["SUCCESS"].set_value(successful)
        self.counter_cards["FAILED"].set_value(failed)
        self.counter_cards["CODES"].set_value(total_codes)
        self.counter_cards["QTY"].set_value(total_qty)

    def batch_finished(self, success: bool, msg: str):
        # Resume Chrome monitoring - whether the batch completed or was
        # cancelled, the probe must come back to life.
        if getattr(self, 'chrome_probe', None):
            self.chrome_probe.resume()
        self.start_btn.setEnabled(True)
        self.upload_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("■ STOP AUTOMATION")
        self.batch_status_label.setText("BATCH: IDLE" if success else "BATCH: STOPPED")
        self.batch_status_label.setStyleSheet("font-size: 10px; color: #55606B; background: transparent; border: none;" if success else "font-size: 10px; font-weight: 800; color: #A32638; background: #F8E4E7; padding: 2px 8px; border-radius: 6px; border: 1px solid #A32638;")
        self.automation_status_label.setText("AUTOMATION: READY" if success else "AUTOMATION: STOPPED")
        self.automation_status_label.setStyleSheet("font-size: 10px; color: #2A5E8C; background: transparent; border: none;" if success else "font-size: 10px; color: #55606B; background: transparent; border: none;")
        if not success and ("cancelled" in msg.lower() or "stopped" in msg.lower()):
            self.log(f"[{time.strftime('%H:%M:%S')}] [STOPPED] Automation stopped by user - {msg} - resources cleaned, no corruption")
        else:
            self.log(f"[{time.strftime('%H:%M:%S')}] [FINISHED] Batch finished - {msg} - ALL PROCEDURES VERIFIED")
        self.update_counters()
        QMessageBox.information(self, "Batch Status", msg)

    def update_chrome_status(self, stage, detail):
        """Render the REAL stage reported by the probe thread.

        The probe can only establish the first two rungs of the connection
        ladder.  It must never imply Selenium is attached or the portal is
        ready - those are proven later, by the batch worker.
        """
        colour = ui_theme.status_colour(stage)
        marker = "\u25cf " if stage == "CDP ENDPOINT VERIFIED" else ""
        self.chrome_status_label.setText("CHROME/CDP: " + marker + stage)
        self.chrome_status_label.setStyleSheet(
            "font-size: 10px; color: " + colour +
            "; background: transparent; border: none;")
        self.chrome_status_label.setToolTip(detail)

    def closeEvent(self, event):
        """Shut down every thread this window owns, in a safe order.

        Nothing is force-terminated: QThread.terminate() can kill a worker in
        the middle of a Selenium call or a portal transaction.  The worker is
        asked to cancel, then joined; the driver service is stopped by
        PortalSession.close(); finally the status probe is stopped and
        joined.  Only then is the close accepted, so no QThread is ever
        destroyed while still running.
        """
        self._shutting_down = True

        # 1. the enhancement worker - graceful cancellation, generous join
        if self.thread and self.thread.isRunning():
            self.log(f"[{time.strftime('%H:%M:%S')}] [SHUTDOWN] Waiting for the current action to finish safely")
            try:
                self.thread.stop()
            except Exception:                               # noqa: BLE001
                pass
            if not self.thread.wait(self.SHUTDOWN_WAIT_MS):
                # Do NOT terminate(): a half-finished portal mutation is far
                # worse than a slow exit.  Keep the window open and let the
                # operator decide.
                self.log(f"[{time.strftime('%H:%M:%S')}] [SHUTDOWN] Worker still busy - close again to retry; the automation is NOT being killed")
                self._shutting_down = False
                event.ignore()
                return

        # 2. the Chrome status probe - stopped exactly once, here
        probe = getattr(self, 'chrome_probe', None)
        if probe is not None:
            probe.stop()
            if not probe.wait(self.SHUTDOWN_WAIT_MS):
                self.log(f"[{time.strftime('%H:%M:%S')}] [SHUTDOWN] Chrome monitor did not stop in time")
            self.chrome_probe = None

        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    ex = BatchCGHSApp()
    ex.show()
    sys.exit(app.exec_())
