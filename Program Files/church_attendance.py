import sys
import os
import json
import logging
import re
import subprocess
import tempfile
import time
import sqlite3
import uuid
from queue import Queue
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Tuple

from PySide6.QtCore import (
    Qt, QTimer, QThread, Signal, QObject, QSize, QRect, Slot
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QStackedWidget, QLabel, QPushButton, QLineEdit, QTextEdit, QComboBox,
    QCheckBox, QSpinBox, QRadioButton, QButtonGroup, QFrame, QMessageBox,
    QFileDialog, QScrollArea, QListWidget, QListWidgetItem, QGroupBox,
    QSplitter, QDialog, QDialogButtonBox, QFormLayout, QDoubleSpinBox,
    QScroller, QGraphicsDropShadowEffect, QTabWidget
)
from PySide6.QtGui import (
    QPixmap, QPainter, QPen, QBrush, QFont, QFontDatabase, QPalette, QColor,
    QCloseEvent, QImage, QIcon, QTextOption, QFontMetrics, QFontInfo
)
from PySide6.QtSvgWidgets import QSvgWidget

import usb.core
import usb.util
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from PIL import Image, ImageDraw, ImageFont, ImageQt
from brother_ql.raster import BrotherQLRaster
from brother_ql.conversion import convert
from brother_ql.backends import backend_factory
from brother_ql.exceptions import BrotherQLError
import socket
import schedule
import threading
import requests
from flask import Flask, jsonify, request as flask_request
from zeroconf import ServiceInfo, Zeroconf, ServiceBrowser

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

LABEL_WIDTH_MM = 62
LABEL_HEIGHT_MM = 29
SCALE_300DPI = 11.811

CUPS_LABEL_MAP = {
    "62x29": "DK-1209",
    "62": "DK-2205",
    "62x100": "DK-1202",
    "29x90": "DK-1201",
    "29x42": "DK-1203",
    "17x54": "DK-1204"
}

def center_window(win: QWidget, w: int = 1200, h: int = 800):
    try:
        screen = QApplication.primaryScreen().availableGeometry()
        win.setGeometry(
            (screen.width() - w) // 2,
            (screen.height() - h) // 2,
            w, h
        )
        win.setMinimumSize(800, 600)
    except Exception as e:
        logging.error(f"Could not center window: {e}")

class DatabaseManager:
    def __init__(self, db_path="attendance_app.sqlite"):
        self.db_path = db_path
        self.conn = None
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            self.cursor = self.conn.cursor()
            self._create_tables()
        except sqlite3.Error as e:
            logging.error(f"Database connection error: {e}")
            raise

    def _create_tables(self):
        try:
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS members (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    first_name TEXT,
                    last_name TEXT,
                    roles TEXT,
                    first_visit TEXT,
                    last_modified_utc TEXT,
                    last_modified_by TEXT
                )
            """)
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS attendance (
                    record_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    date TEXT,
                    time TEXT,
                    program_id TEXT,
                    is_deleted INTEGER DEFAULT 0,
                    deleted_by TEXT,
                    deleted_timestamp_utc TEXT
                )
            """)
            self.conn.commit()
        except sqlite3.Error as e:
            logging.error(f"Error creating tables: {e}")

    def get_all_members(self) -> Dict[str, Dict]:
        self.cursor.execute("SELECT * FROM members")
        rows = self.cursor.fetchall()
        members_dict = {}
        for row in rows:
            member_data = dict(row)
            if member_data.get('roles'):
                member_data['roles'] = json.loads(member_data['roles'])
            members_dict[row['id']] = member_data
        return members_dict

    def replace_all_members(self, members_data: Dict[str, Dict]):
        self.cursor.execute("DELETE FROM members")
        for member_id, member in members_data.items():
            self.cursor.execute("""
                INSERT INTO members (id, name, first_name, last_name, roles, first_visit, last_modified_utc, last_modified_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                member_id,
                member.get('name'),
                member.get('first_name'),
                member.get('last_name'),
                json.dumps(member.get('roles', [])),
                member.get('first_visit'),
                member.get('last_modified_utc'),
                member.get('last_modified_by')
            ))
        self.conn.commit()

    def get_all_attendance(self) -> Dict[str, Dict]:
        self.cursor.execute("SELECT * FROM attendance")
        rows = self.cursor.fetchall()
        return {row['record_id']: dict(row) for row in rows}

    def replace_all_attendance(self, attendance_data: Dict[str, Dict]):
        self.cursor.execute("DELETE FROM attendance")
        for record_id, record in attendance_data.items():
            self.cursor.execute("""
                INSERT INTO attendance (record_id, name, date, time, program_id, is_deleted, deleted_by, deleted_timestamp_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record_id,
                record.get('name'),
                record.get('date'),
                record.get('time'),
                record.get('program_id'),
                1 if record.get('is_deleted') else 0,
                record.get('deleted_by'),
                record.get('deleted_timestamp_utc')
            ))
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()
            logging.info("Database connection closed.")

def migrate_from_json_to_sqlite(db: DatabaseManager):
    migration_flag_file = Path(".migration_complete")
    if migration_flag_file.exists():
        logging.info("Migration has already been completed. Skipping.")
        return

    logging.info("--- Starting one-time migration from JSON to SQLite ---")
    
    try:
        with open('members.json', 'r', encoding='utf-8') as f:
            members_data = json.load(f)
        if members_data:
            db.replace_all_members(members_data)
            logging.info(f"Successfully migrated {len(members_data)} members.")
    except (FileNotFoundError, json.JSONDecodeError):
        logging.info("members.json not found or empty. Skipping member migration.")

    all_attendance = {}
    try:
        with open('attendance.json', 'r', encoding='utf-8') as f:
            attendance_data = json.load(f)
        
        if isinstance(attendance_data, dict) and attendance_data and isinstance(next(iter(attendance_data.values())), list):
            logging.info("Old attendance.json format detected. Converting to new format...")
            converted_count = 0
            for date_str, entries in attendance_data.items():
                for entry in entries:
                    if 'name' not in entry or 'time' not in entry: continue
                    new_record = {
                        "record_id": str(uuid.uuid4()),
                        "program_id": "migrated-legacy-data",
                        "name": entry['name'], "date": date_str, "time": entry['time'],
                        "is_deleted": False, "deleted_by": None, "deleted_timestamp_utc": None
                    }
                    all_attendance[new_record['record_id']] = new_record
                    converted_count += 1
            logging.info(f"Converted {converted_count} legacy attendance records.")
        elif isinstance(attendance_data, dict):
            all_attendance.update(attendance_data)
            
    except (FileNotFoundError, json.JSONDecodeError):
        logging.info("attendance.json not found. Skipping attendance migration.")

    if all_attendance:
        db.replace_all_attendance(all_attendance)
        logging.info(f"Successfully migrated {len(all_attendance)} total attendance records to database.")

    migration_flag_file.touch()
    logging.info("--- Migration complete. Flag file created. ---")

class SplashScreen(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.SplashScreen)
        self.setFixedSize(480, 300)
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        label = QLabel()
        splash_path = Path("splash.png")
        if splash_path.exists():
            pixmap = QPixmap(str(splash_path))
            if not pixmap.isNull():
                label.setPixmap(pixmap)
                self.setFixedSize(pixmap.size())
        else:
            label.setText("Loading...")
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("font-size:24px; color:#0a3d7d; background:white; border:2px solid #ccc")
        layout.addWidget(label)
        center_window(self, self.width(), self.height())

class PrinterInterface:
    def __init__(self, model='QL-700', printer_identifier=None, backend='pyusb', print_system='brother-ql'):
        self.model = model
        self.printer_identifier = printer_identifier
        self.backend = backend
        self.print_system = print_system
        self.printer = None
        self.venv_path = Path("venv")

    def discover_cups_printers(self) -> Tuple[bool, Any]:
        try:
            result = subprocess.run(['lpstat', '-p', '-d'], capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                errmsg = "Could not execute 'lpstat'. Is CUPS installed and running on your system (Linux/macOS)?"
                logging.error(f"{errmsg} Stderr: {result.stderr}")
                return False, errmsg

            printers = []
            default_printer = None
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if line.startswith("printer"):
                    parts = line.split()
                    printer_name = parts[1]
                    printers.append({
                        'identifier': printer_name, 'model_guess': 'CUPS Printer',
                        'backend': 'cups', 'raw_identifier': f"{printer_name} (System Printer)"
                    })
                elif line.startswith("system default destination:"):
                    default_printer = line.split()[-1]

            if default_printer:
                for i, p in enumerate(printers):
                    if p['identifier'] == default_printer:
                        printers.insert(0, printers.pop(i)); break
            
            logging.info(f"Discovered CUPS printers: {[p['identifier'] for p in printers]}")
            return True, printers

        except FileNotFoundError:
            return False, "'lpstat' command not found. This feature is for CUPS-based systems (Linux/macOS)."
        except Exception as e:
            logging.error(f"An unexpected error occurred during CUPS printer discovery: {e}", exc_info=True)
            return False, f"An unexpected error occurred: {e}"

    def discover_printer(self) -> Tuple[bool, Any]:
        discovered_printers = []
        try:
            brother_ql_exe = "brother_ql.exe" if sys.platform == "win32" else "brother_ql"
            brother_ql_path_venv = self.venv_path / ("Scripts" if sys.platform == "win32" else "bin") / brother_ql_exe
            brother_ql_cmd = str(brother_ql_path_venv) if brother_ql_path_venv.exists() else brother_ql_exe
            
            logging.info(f"Discovering printers using command: '{brother_ql_cmd} discover'")
            result = subprocess.run([brother_ql_cmd, "discover"], capture_output=True, text=True, encoding='utf-8', timeout=20)
            if result.stderr:
                logging.debug(f"brother_ql discover stderr: {result.stderr.strip()}")

            if result.returncode == 0 and result.stdout:
                lines = result.stdout.strip().split('\n')
                for raw_line in lines:
                    if not raw_line.strip(): continue
                    
                    raw_identifier = raw_line.strip()
                    current_processing_identifier, model_guess, backend_guess = raw_identifier, "Unknown", "unknown"

                    if " (" in current_processing_identifier and ")" in current_processing_identifier:
                        parts = current_processing_identifier.split(" (", 1)
                        current_processing_identifier = parts[0].strip()
                        model_part_full = parts[1].split(")", 1)[0].strip()
                        model_guess = model_part_full.replace("Brother ", "").replace(" series", "").strip()

                    cleaned_identifier = current_processing_identifier

                    if current_processing_identifier.startswith("usb://0x"):
                        try:
                            scheme, path_part = current_processing_identifier.split("://", 1)
                            vid_str_part, pid_plus_suffix_part = path_part.split(":", 1)
                            
                            pid_potential_value = ""
                            for char_code in pid_plus_suffix_part:
                                if char_code in "/?": break
                                pid_potential_value += char_code
                            
                            pid_hex_digits = ""
                            start_idx = 2 if pid_potential_value.lower().startswith("0x") else 0
                            for char_hex in pid_potential_value[start_idx:]:
                                if char_hex in "0123456789abcdefABCDEF":
                                    pid_hex_digits += char_hex
                                else:
                                    break
                            
                            if pid_hex_digits:
                                cleaned_pid_str = "0x" + pid_hex_digits
                                serial_suffix = ""
                                junk_part_len = len(pid_potential_value)
                                if junk_part_len < len(pid_plus_suffix_part):
                                    serial_suffix = pid_plus_suffix_part[junk_part_len:]
                                
                                cleaned_identifier = f"{scheme}://{vid_str_part}:{cleaned_pid_str}{serial_suffix}"
                                logging.info(f"Cleaned corrupted USB ID. From: '{current_processing_identifier}' | To: '{cleaned_identifier}'")

                        except Exception as e:
                            logging.error(f"ERROR during USB identifier cleaning: {e}")

                    if cleaned_identifier.startswith("usb://"):
                        backend_guess = "pyusb"
                    elif cleaned_identifier.startswith(("tcp://", "net://")):
                        backend_guess = "network"

                    printer_info = {'identifier': cleaned_identifier, 'model_guess': model_guess, 'backend': backend_guess, 'raw_identifier': raw_identifier}
                    
                    if not any(p['identifier'] == printer_info['identifier'] for p in discovered_printers):
                        discovered_printers.append(printer_info)
                        logging.info(f"  Added discovered printer: {printer_info}")

                return True, discovered_printers
            
            elif result.returncode != 0 and ("command not found" in result.stderr.lower() or "not recognized" in result.stderr.lower()):
                return False, f"'{brother_ql_cmd}' not found. Ensure Brother QL tools are installed."

            return True, []

        except subprocess.TimeoutExpired:
            return False, "Printer discovery timed out."
        except FileNotFoundError:
            return False, f"'{brother_ql_cmd}' not found. Ensure Brother QL tools are installed."
        except Exception as e:
            logging.error(f"An unexpected error occurred during printer discovery: {e}", exc_info=True)
            return False, f"An unexpected error occurred: {e}"

        
        
    def connect(self) -> bool:
        if not self.printer_identifier:
            return False

        if self.print_system == 'cups':
            try:
                result_uri = subprocess.run(['lpstat', '-v', self.printer_identifier], capture_output=True, text=True, timeout=5)
                if result_uri.returncode != 0: return False
                
                match = re.search(r'device for .+: (.+)', result_uri.stdout)
                if not match: return False
                device_uri = match.group(1)

                result_devices = subprocess.run(['lpinfo', '-v'], capture_output=True, text=True, timeout=10)
                if result_devices.returncode != 0: return False
                
                return device_uri in result_devices.stdout

            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                logging.error(f"CUPS connection check failed: command not found or timed out. {e}")
                return False
            except Exception as e:
                logging.error(f"CUPS printer check failed for {self.printer_identifier}: {e}")
                return False

        if not self.backend:
            return False
            
        try:
            if self.backend == 'network':
                ident = self.printer_identifier
                if ident.startswith("net://"): return True
                if ident.startswith("tcp://"): ident = ident[len("tcp://"):]
                host, port_str = ident.split(':', 1) if ':' in ident else (ident, '9100')
                with socket.create_connection((host, int(port_str)), timeout=3):
                    return True
            
            elif self.backend == 'pyusb':
                if not self.printer_identifier.startswith("usb://"): return False
                parts = self.printer_identifier.split("://")[1].split("?")[0]
                vid_str, pid_str = parts.split(":")[:2]
                vendor_id, product_id = int(vid_str, 16), int(pid_str, 16)
                return usb.core.find(idVendor=vendor_id, idProduct=product_id) is not None

        except usb.core.NoBackendError:
            logging.error("Libusb backend not found. Please ensure libusb is installed.")
            return False
        except (socket.error, ValueError, TimeoutError, IndexError) as e:
            logging.warning(f"Connection test failed for {self.printer_identifier}: {e}")
            return False
            
        return False

        
    def print_label(self, *, image_path: str, label_type: str) -> Tuple[bool, str]:
        if not self.printer_identifier:
            return False, "No printer identifier configured."
        if not image_path or not Path(image_path).exists():
            return False, f"Image path does not exist: {image_path}"

        if self.print_system == 'cups':
            try:
                cups_media_size = CUPS_LABEL_MAP.get(label_type, "DK-1209")
                cmd = [
                    'lpr',
                    '-P', self.printer_identifier,
                    '-o', f'media={cups_media_size}',
                    str(image_path)
                ]
                logging.info(f"Sending print job to CUPS: {' '.join(cmd)}")
                proc = subprocess.run(cmd, text=True, capture_output=True, timeout=30)
                if proc.returncode != 0:
                    error_msg = (proc.stderr or proc.stdout or "Unknown CUPS 'lpr' error.").strip()
                    logging.error(f"CUPS printing failed: {error_msg}")
                    return False, error_msg
                return True, "Print command sent to CUPS successfully."
            except FileNotFoundError:
                return False, "'lpr' command not found. This feature is for CUPS-based systems."
            except Exception as e:
                logging.error(f"Unexpected CUPS print error: {e}", exc_info=True)
                return False, f"An unexpected error occurred during CUPS printing: {e}"    
        try:
            # --- Start of Corrected brother-ql-next Block ---
            
            # 1. Generate the raster data (this part was already correct).
            qlr = BrotherQLRaster(self.model)
            qlr.exception_on_warning = True
            with Image.open(image_path) as img:
                convert(
                    qlr=qlr, 
                    images=[img], 
                    label=label_type, 
                    rotate='0', 
                    threshold=0.5, 
                    dither=False, 
                    compress=False, 
                    cut=True
                )

            if not qlr.data:
                return False, "Raster conversion resulted in empty data."

            # 2. Import the `send` helper function from the library's backends.
            from brother_ql.backends.helpers import send

            # 3. Call `send` with the raster data, printer identifier, and backend name.
            #    This single function handles backend selection and data transmission.
            logging.info(f"Sending {len(qlr.data)} bytes to {self.printer_identifier} via {self.backend} backend.")
            
            send(
                instructions=qlr.data,
                printer_identifier=self.printer_identifier,
                backend_identifier=self.backend,
                blocking=True
            )
            
            logging.info("Print data sent successfully via brother_ql_next API.")
            return True, "Print command sent successfully via brother_ql."
            # --- End of Corrected brother-ql-next Block ---

        except usb.core.USBError as e:
            # A timeout after sending data via USB is often not a critical error,
            # as the printer may have already accepted the job.
            if 'Timeout' in str(e):
                logging.warning(f"A USB timeout occurred, which is often a false negative. Assuming print was successful for {self.printer_identifier}.")
                return True, "Print command sent (with USB timeout quirk)."
            else:
                logging.error(f"A critical USB error occurred: {e}", exc_info=True)
                return False, f"USB Error: {e}"
        except BrotherQLError as e:
            # This will catch specific printing errors from the library,
            # like "No paper" or "Wrong label type".
            logging.error(f"brother_ql backend error: {e}", exc_info=True)
            return False, f"Printing Error: {e}"
        except Exception as e:
            # Generic catch-all for other unexpected issues.
            logging.error(f"Unexpected brother_ql print error: {e}", exc_info=True)
            return False, f"An unexpected error occurred: {e}"
                
class SyncSignals(QObject):
    ui_refresh_needed = Signal()
    connection_lost_start_discovery = Signal()
    status_updated = Signal(str, str)
    server_data_received = Signal(dict)

class SyncManager:
    SYNC_SERVER_PORT = 5678
    SYNC_SERVICE_TYPE = "_attendance-sync._tcp.local."
    SYNC_POLL_INTERVAL_SECONDS = 45
    SYNC_DISCOVERY_TIMEOUT_MS = 3000

    def __init__(self, app_instance):
        self.app = app_instance
        self.signals = SyncSignals()
        self.server_url = None
        self.is_server_owner = False
        self.flask_server_thread = None
        self.zeroconf = None
        self.browser = None
        self.registered_service_info = None
        self.sync_active = False
        self.periodic_sync_thread = None

        self.config_dir = Path(".")
        self.server_master_attendance_file = self.config_dir / "server_master_attendance.json"
        self.server_master_members_file = self.config_dir / "server_master_members.json"

        self.program_id, self.sync_settings_path = self._get_or_create_program_id()
        logging.info(f"SyncManager initialized with Program ID: {self.program_id}")
        
        self.server_attendance_data_cache = {}
        self.server_members_data_cache = {}
        self.server_master_attendance_file.parent.mkdir(parents=True, exist_ok=True)
        
        self.needs_reconciliation = False
        self.wsgi_server = None

        self.upload_queue = Queue()
        self.uploader_thread = threading.Thread(target=self._uploader_thread_target, daemon=True)
        self.uploader_thread.start()

    def _uploader_thread_target(self):
        while True:
            endpoint, method, data = self.upload_queue.get()
            if endpoint is None:
                break

            if not self.server_url:
                logging.warning(f"No server connection. Action '{endpoint}' will remain in queue.")
                self.upload_queue.task_done()
                continue
            
            try:
                url = f"{self.server_url}/{endpoint}"
                response = requests.request(method.upper(), url, json=data, timeout=10)
                response.raise_for_status()
                logging.info(f"Action '{endpoint}' sent to server successfully.")
            except requests.exceptions.RequestException as e:
                logging.error(f"Failed to send action '{endpoint}' to server: {e}")
            
            self.upload_queue.task_done()
            time.sleep(0.1)

    def _get_or_create_program_id(self) -> Tuple[str, Path]:
        settings_path = self.config_dir / "sync_settings.json"
        settings = {}
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                settings = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logging.warning("sync_settings.json not found or invalid. A new one will be created.")

        if 'program_id' in settings and settings['program_id']:
            return settings['program_id'], settings_path

        new_id = str(uuid.uuid4())
        settings['program_id'] = new_id
        try:
            with open(settings_path, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=4)
            logging.info(f"Generated and saved new Program ID: {new_id}")
        except Exception as e:
            logging.error(f"Could not save new Program ID to {settings_path}: {e}")
        
        return new_id, settings_path

    def _get_local_ip(self) -> str:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('10.255.255.255', 1))
            IP = s.getsockname()[0]
        except Exception:
            try: IP = socket.gethostbyname(socket.gethostname())
            except Exception: IP = '127.0.0.1'
        finally: s.close()
        if IP == '127.0.0.1' and sys.platform.startswith("linux"):
             try:
                result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, check=False)
                if ips := result.stdout.strip().split(): IP = ips[0]
             except Exception: pass
        return IP

    def _load_server_files(self):
        try:
            with open(self.server_master_attendance_file, 'r', encoding='utf-8') as f:
                self.server_attendance_data_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError): self.server_attendance_data_cache = {}
        try:
            with open(self.server_master_members_file, 'r', encoding='utf-8') as f:
                self.server_members_data_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError): self.server_members_data_cache = {}

    def _save_server_files(self):
        try:
            with open(self.server_master_attendance_file, 'w', encoding='utf-8') as f:
                json.dump(self.server_attendance_data_cache, f, indent=4)
            with open(self.server_master_members_file, 'w', encoding='utf-8') as f:
                json.dump(self.server_members_data_cache, f, indent=4)
        except Exception as e:
            logging.error(f"SyncManager (Server): Error saving master files: {e}")

    def _run_flask_app(self):
        from wsgiref.simple_server import make_server
        
        flask_app = Flask(__name__)
        self._load_server_files()

        @flask_app.route('/ping', methods=['GET'])
        def ping(): return "pong", 200

        @flask_app.route('/get_all_data', methods=['GET'])
        def get_all_data():
            return jsonify({
                "attendance": self.server_attendance_data_cache,
                "members": self.server_members_data_cache
            })

        @flask_app.route('/checkin', methods=['POST'])
        def checkin():
            data = flask_request.json
            record_id = data.get('record_id')
            if not record_id: return jsonify({"status": "error", "message": "record_id is required"}), 400
            self.server_attendance_data_cache[record_id] = data
            self._save_server_files()
            return jsonify({"status": "success"}), 201

        @flask_app.route('/update_attendance_record', methods=['PUT'])
        def update_attendance_record():
            data = flask_request.json
            record_id = data.get('record_id')
            if not record_id: return jsonify({"status": "error", "message": "record_id is required"}), 400
            if record_id in self.server_attendance_data_cache:
                self.server_attendance_data_cache[record_id].update(data)
                self._save_server_files()
                return jsonify({"status": "success"}), 200
            return jsonify({"error": "Record not found"}), 404

            
        @flask_app.route('/register_member', methods=['POST'])
        def register_member():
            new_member_data = flask_request.json
            
            member_id = new_member_data.get('id')
            if not member_id:
                return jsonify({"status": "error", "message": "Member record must have a client-generated ID."}), 400

            name_to_check = new_member_data.get('name', '').strip().lower()
            
            existing_member_id = None
            for mid, mdata in self.server_members_data_cache.items():
                if mdata.get('name', '').strip().lower() == name_to_check:
                    existing_member_id = mid
                    break

            if existing_member_id:
                logging.info(f"Sync Merge: Member '{new_member_data.get('name')}' already exists with ID {existing_member_id}. Merging.")
                self.server_members_data_cache[existing_member_id].update(new_member_data)
                self.server_members_data_cache[existing_member_id]['last_modified_utc'] = datetime.now(timezone.utc).isoformat()
                self.server_members_data_cache[existing_member_id]['last_modified_by'] = flask_request.json.get('program_id')
                self._save_server_files()
                return jsonify({"status": "merged", "member_id": existing_member_id}), 200

            else:
                logging.info(f"Sync New: Registering new member '{new_member_data.get('name')}' with ID {member_id}.")
                new_member_data['last_modified_utc'] = datetime.now(timezone.utc).isoformat()
                new_member_data['last_modified_by'] = flask_request.json.get('program_id')
                self.server_members_data_cache[member_id] = new_member_data
                self._save_server_files()
                return jsonify({"status": "success", "member_id": member_id}), 201

        @flask_app.route('/update_member/<member_id>', methods=['PUT'])
        def update_member(member_id):
            if member_id in self.server_members_data_cache:
                updated_data = flask_request.json
                updated_data['last_modified_utc'] = datetime.now(timezone.utc).isoformat()
                updated_data['last_modified_by'] = flask_request.json.get('program_id')
                self.server_members_data_cache[member_id].update(updated_data)
                self._save_server_files()
                return jsonify({"status": "success"}), 200
            return jsonify({"error": "Member not found"}), 404

        @flask_app.route('/delete_member/<member_id>', methods=['DELETE'])
        def delete_member(member_id):
            if member_id in self.server_members_data_cache:
                del self.server_members_data_cache[member_id]
                self._save_server_files()
                return jsonify({"status": "success"}), 200
            return jsonify({"error": "Member not found"}), 404

        try:
            self.wsgi_server = make_server('0.0.0.0', self.SYNC_SERVER_PORT, flask_app)
            logging.info(f"WSGI server starting on 0.0.0.0:{self.SYNC_SERVER_PORT}")
            self.wsgi_server.serve_forever()
            logging.info("WSGI server has shut down.")
        except Exception as e:
            logging.error(f"SyncManager: WSGI server failed: {e}")
        finally:
            self.wsgi_server = None
            self.is_server_owner = False

    def send_checkin_record(self, record: dict):
        self._send_action_to_server('checkin', method='POST', data=record)

    class ServiceListener:
        def __init__(self, sync_manager): self.sync_manager = sync_manager
        def remove_service(self, zeroconf, type, name):
            logging.warning(f"SyncManager: Server {name} disappeared from the network.")
            if not self.sync_manager.is_server_owner:
                self.sync_manager.server_url = None
                self.sync_manager.signals.connection_lost_start_discovery.emit()
        def add_service(self, zeroconf, type, name):
            if self.sync_manager.is_server_owner or self.sync_manager.server_url: return
            if info := zeroconf.get_service_info(type, name):
                if info.addresses:
                    address = socket.inet_ntoa(info.addresses[0])
                    self.sync_manager.server_url = f"http://{address}:{info.port}"
                    logging.info(f"SyncManager (Client): Discovered server at {self.sync_manager.server_url}")
        def update_service(self, zeroconf, type, name):
            pass

    def start(self):
        self.is_server_owner = False
        self.server_url = None
        if self.zeroconf:
            try: self.zeroconf.close()
            except Exception as e: logging.error(f"Error closing previous zeroconf: {e}")
        self.zeroconf = Zeroconf()
        self.browser = ServiceBrowser(self.zeroconf, self.SYNC_SERVICE_TYPE, self.ServiceListener(self))
        logging.info(f"SyncManager: Browsing for services for {self.SYNC_DISCOVERY_TIMEOUT_MS}ms...")
        self.signals.status_updated.emit("Searching for server...", "#0076c6")

    def _check_discovery_and_decide_role(self):
        if self.server_url:
            logging.info(f"SyncManager: Found server: {self.server_url}. Operating as CLIENT.")
            self.is_server_owner = False
            self.needs_reconciliation = True
            self._start_periodic_sync_thread()
            self.signals.status_updated.emit(f"Connected as Client", "#2e7d32")
            if self.zeroconf and self.browser and not self.is_server_owner:
                try: self.zeroconf.close(); self.browser = None
                except Exception: pass
        else:
            logging.info("SyncManager: No server found. Attempting to START new server.")
            self._start_local_server_and_register()

    def _start_local_server_and_register(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s: s.bind(("0.0.0.0", self.SYNC_SERVER_PORT))
        except OSError:
            logging.error(f"SyncManager: Port {self.SYNC_SERVER_PORT} in use. Cannot start server.")
            self.signals.status_updated.emit(f"Port {self.SYNC_SERVER_PORT} in use. Cannot start server.", "#c62828")
            self.signals.connection_lost_start_discovery.emit()
            return

        logging.info("Promoting to server. Loading local database into server cache.")
        self.server_members_data_cache = self.app.db.get_all_members()
        self.server_attendance_data_cache = self.app.db.get_all_attendance()
        self._save_server_files()
        
        self.is_server_owner = True
        self.flask_server_thread = threading.Thread(target=self._run_flask_app, daemon=True)
        self.flask_server_thread.start()
        time.sleep(1.5)
        my_ip = self._get_local_ip()
        self.server_url = f"http://{my_ip}:{self.SYNC_SERVER_PORT}"
        
        try:
            requests.get(f"http://{my_ip}:{self.SYNC_SERVER_PORT}/ping", timeout=2)
            if self.zeroconf: self.zeroconf.close()
            self.zeroconf = Zeroconf()
            service_name = f"AttendanceServer-{my_ip.replace('.', '-')}.{self.SYNC_SERVICE_TYPE}"
            self.registered_service_info = ServiceInfo(
                self.SYNC_SERVICE_TYPE, service_name,
                addresses=[socket.inet_aton(my_ip)], port=self.SYNC_SERVER_PORT
            )
            self.zeroconf.register_service(self.registered_service_info)
            logging.info(f"SyncManager: Service {service_name} registered and running with {len(self.server_members_data_cache)} members.")
            self.signals.status_updated.emit("Running as Server", "#2e7d32")
            self._start_periodic_sync_thread()
        except requests.exceptions.RequestException as e:
            logging.error(f"SyncManager: Local server failed to respond: {e}.")
            self.stop()
            self.signals.status_updated.emit("Local server failed to start.", "#c62828")
            self.signals.connection_lost_start_discovery.emit()

    def stop(self):
        logging.info("SyncManager: Stopping...")
        self.sync_active = False
        self.needs_reconciliation = False

        self.upload_queue.put((None, None, None))

        if self.is_server_owner and self.wsgi_server:
            logging.info("SyncManager: This instance is the server. Shutting it down.")
            try: self.wsgi_server.shutdown()
            except Exception as e: logging.error(f"SyncManager: Error during WSGI server shutdown: {e}")
            if self.flask_server_thread: self.flask_server_thread.join(timeout=3)
            self.flask_server_thread = None
            self.wsgi_server = None

        if self.registered_service_info and self.zeroconf:
            try: self.zeroconf.unregister_service(self.registered_service_info)
            except Exception as e: logging.error(f"Error unregistering service: {e}")
        
        if self.zeroconf:
            try: self.zeroconf.close()
            except Exception as e: logging.error(f"Error closing Zeroconf: {e}")
        
        if self.periodic_sync_thread and self.periodic_sync_thread.is_alive():
            self.periodic_sync_thread.join(timeout=2)
        
        if self.uploader_thread and self.uploader_thread.is_alive():
            self.uploader_thread.join(timeout=2)

        self.is_server_owner = False
        self.server_url = None
        self.signals.status_updated.emit("Disconnected", "#616161")
        logging.info("SyncManager: Stopped.")

    def _sync_cycle_run_and_update_local(self):
        if not self.server_url: return
        try:
            response = requests.get(f"{self.server_url}/get_all_data", timeout=15)
            response.raise_for_status()
            server_state = response.json()
            if not isinstance(server_state, dict) or "attendance" not in server_state or "members" not in server_state:
                logging.error("SyncManager: Server returned invalid data.")
                return
            
            self.signals.server_data_received.emit(server_state)
                
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            logging.warning(f"SyncManager: Connection error with server {self.server_url}. Assuming server down.")
            self.server_url = None
            self.signals.connection_lost_start_discovery.emit()
        except Exception as e:
            logging.error(f"SyncManager: Unexpected error in sync cycle: {e}", exc_info=True)

    def _sync_loop_thread_target(self):
        time.sleep(5)
        while self.sync_active:
            self._sync_cycle_run_and_update_local()
            for _ in range(self.SYNC_POLL_INTERVAL_SECONDS):
                if not self.sync_active: break
                time.sleep(1)

    def _start_periodic_sync_thread(self):
        if self.periodic_sync_thread and self.periodic_sync_thread.is_alive(): return
        self.sync_active = True
        self.periodic_sync_thread = threading.Thread(target=self._sync_loop_thread_target, daemon=True)
        self.periodic_sync_thread.start()
        logging.info("SyncManager: Periodic sync thread started.")

    def _send_action_to_server(self, endpoint, method='POST', data=None):
        if data is None:
            data = {}
        if 'program_id' not in data:
            data['program_id'] = self.program_id
            
        self.upload_queue.put((endpoint, method, data))

    def send_checkin(self, name: str, date: str, time_str: str):
        record = {
            "record_id": str(uuid.uuid4()), "program_id": self.program_id,
            "name": name, "date": date, "time": time_str,
            "is_deleted": False, "deleted_by": None, "deleted_timestamp_utc": None
        }
        self._send_action_to_server('checkin', method='POST', data=record)

    def send_tombstone_attendance(self, record_id: str):
        tombstone_update = {
            "record_id": record_id, "is_deleted": True,
            "deleted_by": self.program_id,
            "deleted_timestamp_utc": datetime.now(timezone.utc).isoformat()
        }
        self._send_action_to_server('update_attendance_record', method='PUT', data=tombstone_update)

    def send_new_member(self, member_data: dict):
        self._send_action_to_server('register_member', method='POST', data=member_data)

    def send_update_member(self, member_id: str, member_data: dict):
        self._send_action_to_server(f'update_member/{member_id}', method='PUT', data=member_data)

    def send_delete_member(self, member_id: str):
        self._send_action_to_server(f'delete_member/{member_id}', method='DELETE')

class MainWindow(QMainWindow):
    reportJobFinished = Signal(bool, str)
    
    def __init__(self, parent=None, db_manager=None):
        super().__init__(parent)
        self.setWindowTitle("Church Attendance Management System")
        
        if not db_manager:
            raise ValueError("A DatabaseManager instance is required.")
        self.db = db_manager

        self.members: Dict[str, Any] = {}
        self.attendance: Dict[str, Any] = {}
        self.nametag_controls: Dict[str, QWidget] = {}
        self.app_fonts: Dict[str, Dict[str, str]] = {}
        
        self.printer_is_connected = False
        self.discovered_printers = []

        self.colors = {
            'primary': '#0a3d7d', 'secondary': '#0076c6', 'accent': '#26b54d',
            'text': '#2b2d42', 'light_bg': '#f8f9fa', 'white': '#ffffff',
            'border': '#eee', 'shadow': '#e6e6e6'
        }
        
        self.sync_manager = SyncManager(self)
        self.sync_manager.signals.ui_refresh_needed.connect(self._handle_sync_ui_refresh)
        self.sync_manager.signals.connection_lost_start_discovery.connect(self._handle_connection_lost)
        self.sync_manager.signals.status_updated.connect(self._update_sync_status)
        self.sync_manager.signals.server_data_received.connect(self._handle_server_data_update)
        self.load_data()
        self.load_branding_settings()
        self.boxes = self.get_default_nametag_settings()
        self.load_nametag_settings() 
        self.init_ui()
        self.schedule_automated_reports()
        self.start_scheduler_thread()
        center_window(self)
        
        QTimer.singleShot(1000, self._auto_connect_on_startup)
        QTimer.singleShot(2500, self.start_sync_discovery)

    def start_sync_discovery(self):
        self.sync_manager.start()
        QTimer.singleShot(
            self.sync_manager.SYNC_DISCOVERY_TIMEOUT_MS,
            self.sync_manager._check_discovery_and_decide_role
        )

    @Slot(dict)
    def _handle_server_data_update(self, server_state: dict):
        logging.info("Main thread received server data. Performing intelligent merge...")
        
        local_members = self.members
        server_members = server_state.get("members", {})
        final_members = local_members.copy()
        
        for server_id, server_member in server_members.items():
            if server_id not in local_members:
                final_members[server_id] = server_member
            else:
                local_ts_str = local_members[server_id].get('last_modified_utc')
                server_ts_str = server_member.get('last_modified_utc')
                if server_ts_str and (not local_ts_str or server_ts_str > local_ts_str):
                    final_members[server_id] = server_member

        for local_id, local_member in local_members.items():
            if local_id not in server_members:
                logging.info(f"Found local-only member '{local_member['name']}'. Uploading.")
                self.sync_manager.send_new_member(local_member)

        local_attendance = self.attendance
        server_attendance = server_state.get("attendance", {})
        final_attendance = local_attendance.copy()
        final_attendance.update(server_attendance)

        for local_id, local_record in local_attendance.items():
            if local_id not in server_attendance:
                logging.info(f"Found local-only attendance for '{local_record['name']}'. Uploading.")
                self.sync_manager.send_checkin_record(local_record)

        try:
            if final_members != self.members or final_attendance != self.attendance:
                logging.info("Data merge resulted in changes. Updating database.")
                self.db.replace_all_members(final_members)
                self.db.replace_all_attendance(final_attendance)
                self.load_data()
                self._handle_sync_ui_refresh()
                logging.info("Database and UI updated successfully from merge.")
            else:
                logging.info("Data merge complete. No changes detected.")
                
        except Exception as e:
            logging.error(f"Failed to update database from merged data: {e}", exc_info=True)

    @Slot(str, str)
    def _update_sync_status(self, message: str, color: str):
        if hasattr(self, 'sync_status_label'):
            self.sync_status_label.setText(message)
            self.sync_status_label.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {color};")

    @Slot()
    def handle_sync_connect(self):
        logging.info("User triggered manual sync re-scan.")
        self.sync_manager.stop()
        QTimer.singleShot(500, self.start_sync_discovery)

        
    @Slot()
    def handle_sync_disconnect(self):
        logging.info("User triggered 'Go Offline'.")
        self.sync_manager.stop()
    
    @Slot()
    def initial_printer_check_and_scan(self):
        logging.info("Performing initial printer status check...")
        try:
            with open('printer_settings.json', 'r') as f:
                settings = json.load(f)
            if not settings.get('printer_identifier'):
                self.printer_is_connected = False
            else:
                printer = PrinterInterface(
                    model=settings.get('model', 'QL-700'),
                    printer_identifier=settings.get('printer_identifier'),
                    backend='network' if settings.get('connection_type') == 'Network' else 'pyusb',
                    print_system='cups' if settings.get('print_system') == 'cups' else 'brother-ql'
                )
                self.printer_is_connected = printer.connect()
        except (FileNotFoundError, json.JSONDecodeError):
            self.printer_is_connected = False
        
        logging.info(f"Printer connection status: {self.printer_is_connected}")
        self._update_printer_status_button()
        
        self._perform_printer_scan()

    def _update_printer_status_button(self):
        if self.printer_is_connected:
            self.printer_status_btn.setText("🟢 Printer Connection")
            self.printer_status_btn.setStyleSheet(f"background-color: #e8f5e9; color: #2e7d32; font-weight: bold; border: 1px solid #a5d6a7; border-radius: 4px; padding: 8px 16px;")
        else:
            self.printer_status_btn.setText("🔴 Printer Connection")
            self.printer_status_btn.setStyleSheet(f"background-color: #ffebee; color: #c62828; font-weight: bold; border: 1px solid #ef9a9a; border-radius: 4px; padding: 8px 16px;")

    def _perform_printer_scan(self):
        logging.info("Starting printer scan...")
        all_printers = []
        iface = PrinterInterface()
        
        success_ql, result_ql = iface.discover_printer()
        if success_ql and isinstance(result_ql, list):
            all_printers.extend(result_ql)
        
        if sys.platform != "win32":
            success_cups, result_cups = iface.discover_cups_printers()
            if success_cups and isinstance(result_cups, list):
                for p_cups in result_cups:
                    if not any(p_ql['identifier'] == p_cups['identifier'] for p_ql in all_printers):
                        all_printers.append(p_cups)
        
        self.discovered_printers = all_printers
        logging.info(f"Scan complete. Found {len(self.discovered_printers)} printers.")

    @Slot()
    def _auto_connect_on_startup(self):
        logging.info("Starting intelligent printer auto-connection...")
        last_used_printer = None
        
        try:
            with open('printer_settings.json', 'r') as f:
                settings = json.load(f)
            if settings.get('printer_identifier'):
                last_used_printer = settings
                printer_iface = PrinterInterface(
                    model=settings.get('model'),
                    printer_identifier=settings.get('printer_identifier'),
                    backend=settings.get('backend'),
                    print_system=settings.get('print_system')
                )
                if printer_iface.connect():
                    logging.info(f"Successfully reconnected to last-used printer: {settings['printer_identifier']}")
                    self.handle_printer_selected(settings, silent=True, is_known_connected=True)
                    return
        except (FileNotFoundError, json.JSONDecodeError):
            logging.info("No printer settings file found. Will scan for a new printer.")
        
        logging.info("Last-used printer not available. Scanning for an alternative...")
        self._perform_printer_scan()
        
        sorted_printers = sorted(self.discovered_printers, key=lambda p: p.get('backend') != 'cups')

        for printer_info in sorted_printers:
            printer_iface = PrinterInterface(
                model=printer_info.get('model_guess'),
                printer_identifier=printer_info.get('identifier'),
                backend=printer_info.get('backend'),
                print_system='cups' if printer_info.get('backend') == 'cups' else 'brother-ql'
            )
            if printer_iface.connect():
                logging.info(f"Found and auto-connected to available printer: {printer_info['identifier']}")
                self.handle_printer_selected(printer_info, silent=True)
                return

        logging.warning("Auto-connection failed. No available printers found.")
        self.printer_is_connected = False
        self._update_printer_status_button()

    @Slot()
    def show_printer_selection_dialog(self):
        self._perform_printer_scan()

        printers_with_status = []
        current_printer_name = ""
        
        connected_id = self.printer_id_edit.text() if self.printer_is_connected else None
        
        for p_info in self.discovered_printers:
            p_info_copy = p_info.copy()
            if self.printer_is_connected and p_info_copy['identifier'] == connected_id:
                p_info_copy['status'] = 'Connected'
                current_printer_name = p_info_copy.get('raw_identifier', connected_id)
            else:
                iface = PrinterInterface(
                    model=p_info_copy.get('model_guess'),
                    printer_identifier=p_info_copy.get('identifier'),
                    backend=p_info_copy.get('backend'),
                    print_system='cups' if p_info_copy.get('backend') == 'cups' else 'brother-ql'
                )
                p_info_copy['status'] = 'Available' if iface.connect() else 'Offline'
            
            printers_with_status.append(p_info_copy)

        dialog = PrinterSelectionDialog(
            printers=printers_with_status,
            current_printer_name=current_printer_name,
            parent=self
        )
        dialog.printer_selected.connect(self.handle_printer_selected)
        dialog.exec()

    @Slot(dict)
    def handle_printer_selected(self, printer_info: dict, silent: bool = False, is_known_connected: bool = False):
        logging.info(f"Setting active printer to: {printer_info}")
        
        backend = printer_info.get('backend', 'pyusb')
        
        self.ps_cups_radio.setChecked(backend == 'cups')
        self.ps_brother_ql_radio.setChecked(backend != 'cups')
        self.conn_net_radio.setChecked(backend == 'network')
        self.conn_usb_radio.setChecked(backend == 'pyusb')
        self.printer_id_edit.setText(printer_info.get('identifier', ''))
        if model := printer_info.get('model_guess') or printer_info.get('model'):
             if model != 'Unknown' and model in [self.model_combo.itemText(i) for i in range(self.model_combo.count())]:
                self.model_combo.setCurrentText(model)

        self.save_printer_settings()

        if is_known_connected:
            self.printer_is_connected = True
        else:
            self.printer_is_connected = PrinterInterface(
                model=self.model_combo.currentText(),
                printer_identifier=self.printer_id_edit.text(),
                backend=backend,
                print_system='cups' if backend == 'cups' else 'brother-ql'
            ).connect()
        
        self._update_printer_status_button()

        if not silent:
            if self.printer_is_connected:
                QMessageBox.information(self, "Success", f"Successfully connected to {printer_info.get('raw_identifier')}.")
            else:
                QMessageBox.warning(self, "Connection Failed", f"Could not establish a connection to {printer_info.get('raw_identifier')}.")
                
    @Slot()
    def _handle_sync_ui_refresh(self):
        logging.info("UI refresh triggered by SyncManager.")
        self.update_dashboard()
        self.update_checkin_lists()
        self.populate_profile_list()
        if hasattr(self, 'admin_chart_ax'):
            self.update_admin_chart()

    @Slot()
    def _handle_connection_lost(self):
        logging.info("Connection lost signal received. Triggering new discovery round.")
        QTimer.singleShot(1000, self.start_sync_discovery)

    def showReportJobResult(self, success: bool, message: str):
        if success:
            QMessageBox.information(self, "Automated Report", message)
        else:
            QMessageBox.critical(self, "Automated Report Error", f"The automated report failed:\n{message}")

    def load_data(self):
        try:
            self.members = self.db.get_all_members()
            self.attendance = self.db.get_all_attendance()
            logging.info(f"Loaded {len(self.members)} members and {len(self.attendance)} attendance records from database.")
        except Exception as e:
            logging.error(f"Failed to load data from database: {e}")
            self.members = {}
            self.attendance = {}

    def save_data(self, filename: str, data: dict):
        try:
            with open(filename, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Could not save {filename}: {e}")

    def load_nametag_settings(self):
        new_boxes_config = self.get_default_nametag_settings()
        try:
            with open('nametag_settings.json', 'r') as f: saved_settings = json.load(f)
            saved_boxes = saved_settings.get('boxes', {})
            if isinstance(saved_boxes, dict):
                if name_box := saved_boxes.get('name', {}):
                    if isinstance(name_box, dict): new_boxes_config['name'].update(name_box)
                if role_box := saved_boxes.get('role', {}):
                    if isinstance(role_box, dict): new_boxes_config['role'].update(role_box)
                logging.info("Successfully loaded and merged saved nametag settings.")
        except (FileNotFoundError, json.JSONDecodeError):
            logging.info("No saved nametag settings found or file was invalid. Using defaults.")
        self.boxes = new_boxes_config

    def save_nametag_settings(self):
        self.update_box_from_controls()
        self.save_data('nametag_settings.json', {'boxes': self.boxes})
        QMessageBox.information(self, "Success", "Name tag settings saved.")

    def init_ui(self):
        self.reportJobFinished.connect(self.showReportJobResult)
        self.setStyleSheet(f"""
            QWidget {{ font-family: Nunito; font-size: 11pt; color: {self.colors['text']}; }}
            QScrollArea {{ border: none; }}
            QMainWindow {{ background-color: {self.colors['light_bg']}; }}
            QGroupBox {{ font-weight: bold; font-size: 12pt; margin-top: 1ex; }}
            QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top center; padding: 0 3px; }}
            QPushButton {{ border: none; padding: 8px 16px; font-weight: bold; border-radius: 4px; }}
            QPushButton[cssClass="primary"] {{ background-color: {self.colors['primary']}; color: white; font-size: 12pt; }}
            QPushButton[cssClass="primary"]:hover {{ background-color: {self.colors['secondary']}; }}
            QPushButton[cssClass="secondary"] {{ background-color: {self.colors['secondary']}; color: white; }}
            QPushButton[cssClass="secondary"]:hover {{ background-color: {self.colors['primary']}; }}
            QPushButton[cssClass="accent"] {{ background-color: {self.colors['accent']}; color: white; }}
            QPushButton[cssClass="accent"]:hover {{ background-color: #218838; }}
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ padding: 5px; border: 1px solid #ccc; border-radius: 4px; }}
            QFrame[class="option-card"], QFrame[class="stat-card"] {{ background-color: {self.colors['white']}; border-radius: 8px; border: 1px solid {self.colors['border']}; }}
            QFrame[class="option-card"]:hover {{ border: 2px solid {self.colors['secondary']}; }}
            QPushButton[class="quick-select-btn"] {{ background-color: {self.colors['white']}; color: {self.colors['primary']}; font-size: 13pt; font-weight: bold; border: 1px solid #ccc; border-radius: 6px; padding: 10px; }}
            QPushButton[class="quick-select-btn"]:hover {{ background-color: #e9e9e9; border-color: {self.colors['secondary']}; }}
            QPushButton[class="quick-select-btn"]:pressed {{ background-color: {self.colors['secondary']}; color: white; }}
            QPushButton[class="keyboard-key"] {{ background-color: {self.colors['white']}; color: {self.colors['text']}; font-size: 14pt; font-weight: bold; border: 1px solid #ccc; border-radius: 6px; }}
            QPushButton[class="keyboard-key"]:hover {{ background-color: #e9e9e9; }}
            QPushButton[class="keyboard-key"]:pressed {{ background-color: {self.colors['secondary']}; color: white; border-color: {self.colors['primary']}; }}
        """)
        central = QWidget(self); self.setCentralWidget(central)
        layout = QVBoxLayout(central); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
        layout.addWidget(self.create_top_navigation())
        self.stack = QStackedWidget(); layout.addWidget(self.stack)
        self.home_page, self.manual_checkin_page, self.new_attendee_page, self.profile_page, self.admin_page = (
            self.create_home_page(), self.create_manual_checkin_page(), self.create_new_attendee_page(),
            self.create_profile_page(), self.create_admin_page()
        )
        for page in [self.home_page, self.manual_checkin_page, self.new_attendee_page, self.profile_page, self.admin_page]:
            self.stack.addWidget(page)
        self.home_btn.clicked.connect(lambda: self.stack.setCurrentWidget(self.home_page))
        self.profile_btn.clicked.connect(lambda: self.stack.setCurrentWidget(self.profile_page))
        self.stack.currentChanged.connect(self.update_active_nav_button)
        self.update_active_nav_button(); self.update_dashboard()
        self.dashboard_timer = QTimer(self); self.dashboard_timer.timeout.connect(self.update_dashboard)
        self.dashboard_timer.start(5000)

    def load_branding_settings(self):
        try:
            with open('branding_settings.json', 'r') as f: settings = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError): settings = {}
        self.welcome_message = settings.get("welcome_message", "Welcome")
        self.logo_path = settings.get("logo_path", "logo.png")
        self.logo_fallback_text = settings.get("logo_fallback_text", "App")

    def save_branding_settings(self):
        settings = {"welcome_message": self.welcome_message_edit.text(), "logo_path": self.logo_path_edit.text(), "logo_fallback_text": self.logo_fallback_edit.text()}
        self.save_data('branding_settings.json', settings)
        self.load_branding_settings()
        if welcome_banner := self.findChild(QLabel, "welcome_banner"): welcome_banner.setText(self.welcome_message)
        if logo_container := self.findChild(QWidget, "logo_container"):
            if old_layout := logo_container.layout():
                while old_layout.count():
                    if (item := old_layout.takeAt(0)) and (widget := item.widget()): widget.deleteLater()
                old_layout.addWidget(self.create_logo_widget())
        QMessageBox.information(self, "Success", "Branding settings saved and updated.")

    def create_top_navigation(self) -> QFrame:
        frame = QFrame(); frame_layout = QVBoxLayout(frame); frame_layout.setContentsMargins(0,0,0,0); frame_layout.setSpacing(0)
        top_bar = QLabel(self.welcome_message); top_bar.setObjectName("welcome_banner")
        top_bar.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        top_bar.setStyleSheet(f"background:{self.colors['primary']}; color:white; font-size:14px; padding:10px 40px; font-weight:bold;")
        nav_bar = QFrame(); nav_bar.setStyleSheet(f"background: {self.colors['white']}; border-bottom: 1px solid {self.colors['border']};"); nav_bar.setFixedHeight(65)
        nav_layout = QHBoxLayout(nav_bar); nav_layout.setContentsMargins(40, 5, 40, 5)
        logo_container = QWidget(); logo_container.setObjectName("logo_container")
        logo_layout = QHBoxLayout(logo_container); logo_layout.setContentsMargins(0,0,0,0); logo_layout.addWidget(self.create_logo_widget())
        nav_layout.addWidget(logo_container, 0, Qt.AlignVCenter); nav_layout.addStretch()
        self.home_btn = QPushButton("Home"); self.profile_btn = QPushButton("Edit Profile")
        self.nav_buttons = [self.home_btn, self.profile_btn]; self.nav_button_group = QButtonGroup(self)
        for i, btn in enumerate(self.nav_buttons):
            btn.setCheckable(True); btn.setAutoExclusive(True)
            btn.setStyleSheet("""QPushButton { color: #0a3d7d; font-weight: bold; margin-left: 10px; background: transparent; padding: 10px; border-bottom: 3px solid transparent; } QPushButton:checked, QPushButton:hover { color: #0076c6; border-bottom: 3px solid #0076c6; }""")
            nav_layout.addWidget(btn); self.nav_button_group.addButton(btn, i)
        frame_layout.addWidget(top_bar); frame_layout.addWidget(nav_bar); self.home_btn.setChecked(True)
        return frame
    
    def create_logo_widget(self) -> QWidget:
        LOGO_MAX_HEIGHT = 50; logo_file = Path(self.logo_path)
        if logo_file.exists():
            if logo_file.suffix.lower() == ".svg":
                logo_widget = QSvgWidget(str(logo_file))
                if renderer := logo_widget.renderer():
                    if not (view_box := renderer.viewBoxF()).isNull() and view_box.height() > 0:
                        logo_widget.setFixedSize(int(LOGO_MAX_HEIGHT * (view_box.width()/view_box.height())), LOGO_MAX_HEIGHT)
                        return logo_widget
            else:
                if not (pixmap := QPixmap(str(logo_file))).isNull():
                    lbl = QLabel(); lbl.setPixmap(pixmap.scaledToHeight(LOGO_MAX_HEIGHT, Qt.SmoothTransformation)); return lbl
        fallback = QLabel(self.logo_fallback_text); fallback.setStyleSheet(f"font-size:24px; font-weight:bold; color:{self.colors['primary']};"); return fallback

    def create_home_page(self) -> QWidget:
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame); QScroller.grabGesture(scroll.viewport(), QScroller.LeftMouseButtonGesture)
        page = QWidget(); scroll.setWidget(page); outer_layout = QHBoxLayout(page); outer_layout.addStretch()
        container = QFrame(); container.setMaximumWidth(1100); lay = QVBoxLayout(container); lay.setContentsMargins(20,40,20,40); lay.setSpacing(40); lay.setAlignment(Qt.AlignTop)
        outer_layout.addWidget(container); outer_layout.addStretch()
        title = QLabel("Check-In Options"); title.setStyleSheet(f"font-size:22px; font-weight:bold; color:{self.colors['primary']}; border: none;"); title.setAlignment(Qt.AlignCenter); lay.addWidget(title)
        cards_layout = QHBoxLayout(); cards_layout.setSpacing(30); cards_layout.addStretch()
        cards_layout.addWidget(self.create_option_card("📋", "Returnee Check-in", "Choose name from roster", lambda: self.stack.setCurrentWidget(self.manual_checkin_page)))
        cards_layout.addWidget(self.create_option_card("🙋", "New Attendee", "Register as first-time visitor", lambda: self.stack.setCurrentWidget(self.new_attendee_page)))
        cards_layout.addStretch(); lay.addLayout(cards_layout)
        stats_title = QLabel("Today's Stats"); stats_title.setStyleSheet(f"font-size:22px; font-weight:bold; color:{self.colors['primary']}; border: none; margin-top: 20px;"); stats_title.setAlignment(Qt.AlignCenter); lay.addWidget(stats_title)
        stats_layout = QHBoxLayout(); stats_layout.setSpacing(20)
        self.today_attendance_lbl, self.new_visitors_lbl, self.total_members_lbl, self.avg_attendance_lbl = QLabel("0"), QLabel("0"), QLabel("0"), QLabel("0")
        stats_layout.addWidget(self.create_stat_card("Today's Attendance", self.today_attendance_lbl)); stats_layout.addWidget(self.create_stat_card("New Visitors", self.new_visitors_lbl))
        stats_layout.addWidget(self.create_stat_card("Regular Attendees", self.total_members_lbl)); stats_layout.addWidget(self.create_stat_card("Average Attendance", self.avg_attendance_lbl))
        lay.addLayout(stats_layout); lay.addStretch(1)

        status_layout = QHBoxLayout()
        status_layout.addStretch()
        self.sync_status_label = QLabel("Initializing Sync...")
        self.sync_status_label.setStyleSheet("font-size: 11pt; font-weight: bold; color: #616161;")
        self.sync_status_label.setAlignment(Qt.AlignCenter)
        status_layout.addWidget(self.sync_status_label)
        status_layout.addStretch()
        lay.addLayout(status_layout)
        
        admin_btn_layout = QHBoxLayout()
        admin_btn_layout.setSpacing(10)
        admin_btn_layout.addStretch()

        sync_connect_btn = QPushButton("Connect / Re-Scan")
        sync_connect_btn.setFixedSize(180, 44)
        sync_connect_btn.setProperty("cssClass", "accent")
        sync_connect_btn.clicked.connect(self.handle_sync_connect)
        admin_btn_layout.addWidget(sync_connect_btn)

        sync_disconnect_btn = QPushButton("Go Offline")
        sync_disconnect_btn.setFixedSize(180, 44)
        sync_disconnect_btn.setStyleSheet(f"background-color: #ff9800; color: white; font-weight: bold; border-radius: 4px; padding: 8px 16px;")
        sync_disconnect_btn.clicked.connect(self.handle_sync_disconnect)
        admin_btn_layout.addWidget(sync_disconnect_btn)

        self.printer_status_btn = QPushButton("⚫ Printer Connection")
        self.printer_status_btn.setFixedSize(220, 44)
        self.printer_status_btn.clicked.connect(self.show_printer_selection_dialog)
        self._update_printer_status_button()
        admin_btn_layout.addWidget(self.printer_status_btn)

        admin_btn = QPushButton("Admin Dashboard")
        admin_btn.setFixedSize(200, 44)
        admin_btn.setProperty("cssClass", "primary")
        admin_btn.clicked.connect(lambda: self.stack.setCurrentWidget(self.admin_page))
        admin_btn_layout.addWidget(admin_btn)
        admin_btn_layout.addStretch()
        lay.addLayout(admin_btn_layout)
        return scroll

    def create_option_card(self, icon: str, title_text: str, desc: str, slot) -> QFrame:
        card = QFrame(); card.setFixedSize(350, 250); card.setCursor(Qt.PointingHandCursor); card.setProperty("class", "option-card")
        layout = QVBoxLayout(card); layout.setContentsMargins(20,20,20,20); layout.setSpacing(15); layout.setAlignment(Qt.AlignCenter)
        icon_label = QLabel(icon); icon_label.setStyleSheet("font-size: 60px; background: transparent; color: #0076c6;")
        title_label = QLabel(title_text); title_label.setStyleSheet("font-weight: bold; font-size: 18pt; background: transparent;")
        desc_label = QLabel(desc); desc_label.setWordWrap(True); desc_label.setAlignment(Qt.AlignCenter); desc_label.setStyleSheet("background: transparent; color: #555; font-size: 12pt;")
        layout.addWidget(icon_label, 0, Qt.AlignCenter); layout.addWidget(title_label, 1, Qt.AlignCenter); layout.addWidget(desc_label, 2, Qt.AlignCenter)
        shadow = QGraphicsDropShadowEffect(self); shadow.setBlurRadius(30); shadow.setXOffset(0); shadow.setYOffset(5); shadow.setColor(QColor(0,0,0,40)); card.setGraphicsEffect(shadow)
        card.mouseReleaseEvent = lambda event: slot() if card.rect().contains(event.position().toPoint()) else None
        return card

    def create_stat_card(self, title: str, value_label: QLabel) -> QFrame:
        card = QFrame(); card.setFixedWidth(220); card.setMinimumHeight(120); card.setProperty("class", "stat-card")
        layout = QVBoxLayout(card); layout.setContentsMargins(15,15,15,15); layout.setAlignment(Qt.AlignCenter)
        title_label = QLabel(title); title_label.setAlignment(Qt.AlignCenter); title_label.setStyleSheet("font-weight: bold; color: #555; background: transparent;")
        value_label.setAlignment(Qt.AlignCenter); value_label.setStyleSheet(f"font-size: 40pt; font-weight: bold; background: transparent; color: {self.colors['primary']};")
        layout.addWidget(title_label); layout.addWidget(value_label)
        shadow = QGraphicsDropShadowEffect(self); shadow.setBlurRadius(20); shadow.setXOffset(0); shadow.setYOffset(4); shadow.setColor(QColor(0,0,0,30)); card.setGraphicsEffect(shadow)
        return card
        
    def create_manual_checkin_page(self) -> QWidget:
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame); QScroller.grabGesture(scroll.viewport(), QScroller.LeftMouseButtonGesture)
        page = QWidget(); scroll.setWidget(page)
        layout = QVBoxLayout(page); layout.setContentsMargins(40,20,40,20); layout.setSpacing(15)
        title = QLabel("Returnee Check-in"); title.setStyleSheet(f"font-size:22px; font-weight:bold; color:{self.colors['primary']};"); layout.addWidget(title, alignment=Qt.AlignCenter)
        self.checkin_search_edit = QLineEdit(); self.checkin_search_edit.setPlaceholderText("Type a name to search..."); self.checkin_search_edit.setStyleSheet("font-size: 16px; padding: 12px; border-radius: 6px;"); layout.addWidget(self.checkin_search_edit)
        quick_select_layout = QHBoxLayout(); quick_select_layout.setSpacing(15)
        self.quick_select_buttons = []
        for _ in range(3):
            btn = QPushButton(); btn.setMinimumHeight(50); btn.setProperty("class", "quick-select-btn"); btn.setVisible(False); quick_select_layout.addWidget(btn); self.quick_select_buttons.append(btn)
        layout.addLayout(quick_select_layout); layout.addWidget(self.create_virtual_keyboard(self.checkin_search_edit))
        splitter = QSplitter(Qt.Horizontal); left = QWidget(); left_layout = QVBoxLayout(left); left_layout.addWidget(QLabel("All Members", alignment=Qt.AlignCenter)); self.all_members_list = QListWidget(); left_layout.addWidget(self.all_members_list); splitter.addWidget(left)
        right = QWidget(); right_layout = QVBoxLayout(right); right_layout.addWidget(QLabel("Currently Checked In", alignment=Qt.AlignCenter)); self.checked_in_list = QListWidget(); right_layout.addWidget(self.checked_in_list); splitter.addWidget(right); layout.addWidget(splitter)
        bottom = QHBoxLayout(); checkin_btn = QPushButton("Check In Selected"); checkin_btn.setProperty("cssClass", "primary"); remove_btn = QPushButton("Remove Check-in"); remove_btn.setProperty("cssClass", "secondary"); back_btn = QPushButton("Back to Home")
        bottom.addStretch(); bottom.addWidget(checkin_btn); bottom.addWidget(remove_btn); bottom.addWidget(back_btn); bottom.addStretch(); layout.addLayout(bottom)
        self.checkin_search_edit.textChanged.connect(self.update_checkin_lists); back_btn.clicked.connect(lambda: self.stack.setCurrentWidget(self.home_page)); checkin_btn.clicked.connect(self.handle_checkin_selected); remove_btn.clicked.connect(self.handle_remove_checkin)
        self.checked_in_list_timer = QTimer(self); self.checked_in_list_timer.timeout.connect(self.update_checked_in_list_display)
        self.stack.currentChanged.connect(lambda idx: (self.checked_in_list_timer.start(30000), self.update_checkin_lists()) if self.stack.widget(idx) == scroll else self.checked_in_list_timer.stop())
        return scroll
        
    def create_virtual_keyboard(self, target_line_edit: QLineEdit) -> QWidget:
        keyboard = QWidget(); main_layout = QVBoxLayout(keyboard); main_layout.setSpacing(10)
        keys = ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]; key_size = 60; stagger_offsets = [0, 35, 75] 
        for i, row_str in enumerate(keys):
            row = QHBoxLayout(); row.setSpacing(10); row.addStretch(); row.addSpacing(stagger_offsets[i])
            for char in row_str:
                btn = QPushButton(char); btn.setProperty("class", "keyboard-key"); btn.setFixedSize(key_size, key_size); btn.clicked.connect(lambda _, l=char: target_line_edit.setText(target_line_edit.text() + l)); row.addWidget(btn)
            row.addStretch(); main_layout.addLayout(row)
        bottom = QHBoxLayout(); bottom.setSpacing(10); space = QPushButton("Space"); space.setProperty("class", "keyboard-key"); space.setFixedSize(450, key_size); space.clicked.connect(lambda: target_line_edit.insert(" "))
        back = QPushButton("⌫"); back.setProperty("class", "keyboard-key"); back.setFixedSize(90, key_size); back.clicked.connect(target_line_edit.backspace)
        bottom.addStretch(); bottom.addWidget(space); bottom.addWidget(back); bottom.addStretch(); main_layout.addLayout(bottom)
        return keyboard
        
    def create_new_attendee_page(self) -> QWidget:
        page = QWidget(); layout = QFormLayout(page); page.setContentsMargins(40,40,40,40)
        title = QLabel("Register as New Attendee"); title.setStyleSheet(f"font-size:22px; font-weight:bold; color:{self.colors['primary']};"); layout.addRow(title)
        self.new_first_name_edit = QLineEdit(); self.new_last_name_edit = QLineEdit(); self.new_role_combo = QComboBox()
        self.new_role_combo.addItems(["Visitor", "Regular Attendee", "Congregation Member", "Sunday School Teacher", "TYF Leader", "PLUS Leader", "Small Group Leader", "Committee Member", "Diaconate Member", "Elder", "Pastor"])
        layout.addRow("First Name:", self.new_first_name_edit); layout.addRow("Last Name:", self.new_last_name_edit); layout.addRow("Church Role:", self.new_role_combo)
        btn_layout = QHBoxLayout(); reg_checkin = QPushButton("Register & Check In"); reg_checkin.setProperty("cssClass", "primary"); reg_only = QPushButton("Register Only"); reg_only.setProperty("cssClass", "secondary"); back = QPushButton("Back")
        btn_layout.addWidget(reg_checkin); btn_layout.addWidget(reg_only); btn_layout.addWidget(back); layout.addRow(btn_layout)
        reg_checkin.clicked.connect(lambda: self.handle_registration(check_in=True)); reg_only.clicked.connect(lambda: self.handle_registration(check_in=False)); back.clicked.connect(lambda: self.stack.setCurrentWidget(self.home_page))
        return page

    def create_profile_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(40,20,40,20)
        title = QLabel("Profile Management"); title.setStyleSheet(f"font-size:24px; font-weight:bold; color:{self.colors['primary']};"); layout.addWidget(title, alignment=Qt.AlignCenter)
        search_layout = QHBoxLayout(); search_layout.addWidget(QLabel("Search Profiles:")); self.profile_search_edit = QLineEdit(); search_layout.addWidget(self.profile_search_edit); layout.addLayout(search_layout)
        splitter = QSplitter(Qt.Horizontal); splitter.setChildrenCollapsible(False); layout.addWidget(splitter)
        self.profile_list = QListWidget(); splitter.addWidget(self.profile_list)
        self.profile_edit_area = QScrollArea(); self.profile_edit_area.setWidgetResizable(True); self.profile_edit_area.setMinimumWidth(400); splitter.addWidget(self.profile_edit_area)
        splitter.setStretchFactor(0, 1); splitter.setStretchFactor(1, 1); self.populate_profile_list(); self.profile_search_edit.textChanged.connect(self.populate_profile_list)
        self.profile_list.itemSelectionChanged.connect(self.display_selected_profile)
        back = QPushButton("Back to Home"); back.clicked.connect(lambda: self.stack.setCurrentWidget(self.home_page)); layout.addWidget(back, alignment=Qt.AlignLeft)
        return page

    def create_admin_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 20, 40, 20)
        layout.setSpacing(15)

        title = QLabel("Admin Dashboard")
        title.setStyleSheet(f"font-size:24px; font-weight:bold; color:{self.colors['primary']};")
        layout.addWidget(title, alignment=Qt.AlignCenter)

        tabs = QTabWidget()
        
        tabs.addTab(self.create_printer_settings_section(), "Printer Settings")
        tabs.addTab(self.create_branding_section(), "Branding")
        tabs.addTab(self.create_nametag_config_section(), "Name Tag Editor")
        tabs.addTab(self.create_reports_section(), "Manual Reports")
        tabs.addTab(self.create_automated_reports_section(), "Automated Reports")
        tabs.addTab(self.create_email_settings_section(), "Email Settings")
        tabs.addTab(self.create_admin_chart(), "Attendance Chart")

        layout.addWidget(tabs)

        back = QPushButton("Back to Home")
        back.clicked.connect(lambda: self.stack.setCurrentWidget(self.home_page))
        layout.addWidget(back, alignment=Qt.AlignLeft)
        layout.addStretch()

        return page

    def create_branding_section(self) -> QGroupBox:
        box = QGroupBox("Branding and Welcome Message"); layout = QFormLayout(box)
        self.welcome_message_edit = QLineEdit(self.welcome_message); layout.addRow("Welcome Banner Text:", self.welcome_message_edit)
        logo_layout = QHBoxLayout(); self.logo_path_edit = QLineEdit(self.logo_path); browse_btn = QPushButton("Browse...")
        def open_logo():
            if fname := QFileDialog.getOpenFileName(self, "Select Logo", "", "Image Files (*.png *.jpg *.jpeg *.svg)")[0]: self.logo_path_edit.setText(fname)
        browse_btn.clicked.connect(open_logo); logo_layout.addWidget(self.logo_path_edit); logo_layout.addWidget(browse_btn); layout.addRow("Logo File Path:", logo_layout)
        self.logo_fallback_edit = QLineEdit(self.logo_fallback_text); layout.addRow("Logo Fallback Text:", self.logo_fallback_edit)
        save = QPushButton("Save Branding Settings"); save.setProperty("cssClass", "primary"); save.clicked.connect(self.save_branding_settings); layout.addRow(save)
        return box

    def create_reports_section(self) -> QGroupBox:
        box = QGroupBox("Manual Attendance Reports"); layout = QFormLayout(box)
        self.start_date_edit = QLineEdit(datetime.now().strftime('%Y-%m-%d')); self.end_date_edit = QLineEdit(datetime.now().strftime('%Y-%m-%d')); self.report_recipients_edit = QLineEdit()
        try:
            with open('email_settings.json') as f: self.report_recipients_edit.setText(', '.join(json.load(f).get('default_recipients', [])))
        except Exception: pass
        gen_btn = QPushButton("Generate Report"); gen_btn.setProperty("cssClass", "primary"); gen_btn.clicked.connect(lambda: self.generate_report(self.start_date_edit.text(), self.end_date_edit.text(), False))
        mail_btn = QPushButton("Generate & Email"); mail_btn.setProperty("cssClass", "secondary"); mail_btn.clicked.connect(lambda: self.generate_report(self.start_date_edit.text(), self.end_date_edit.text(), True, self.report_recipients_edit.text()))
        btn_layout = QHBoxLayout(); btn_layout.addWidget(gen_btn); btn_layout.addWidget(mail_btn); layout.addRow("Start Date:", self.start_date_edit); layout.addRow("End Date:", self.end_date_edit); layout.addRow("Email Recipients:", self.report_recipients_edit); layout.addRow(btn_layout)
        return box

    def create_automated_reports_section(self) -> QGroupBox:
        box = QGroupBox("Automated Reports"); layout = QFormLayout(box)
        self.auto_freq_combo = QComboBox(); self.auto_freq_combo.addItems(["Daily", "Weekly", "Bi-Weekly", "Monthly"]); self.auto_day_combo = QComboBox(); self.auto_day_combo.addItems(["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"])
        self.auto_hour_spin, self.auto_min_spin, self.auto_days_spin = QSpinBox(), QSpinBox(), QSpinBox(); self.auto_hour_spin.setRange(0,23); self.auto_min_spin.setRange(0,59); self.auto_days_spin.setRange(1,31); self.auto_days_spin.setValue(7)
        self.auto_recipients_edit = QTextEdit()
        try:
            with open("report_automation.json") as f: s = json.load(f)
            self.auto_freq_combo.setCurrentText(s.get("frequency", "Weekly")); self.auto_day_combo.setCurrentText(s.get("day", "Sunday")); self.auto_hour_spin.setValue(s.get("hour", 13))
            self.auto_min_spin.setValue(s.get("minute", 30)); self.auto_days_spin.setValue(s.get("days_to_include", 7)); self.auto_recipients_edit.setPlainText(", ".join(s.get("recipients", [])))
        except Exception: pass
        time_layout = QHBoxLayout(); time_layout.addWidget(self.auto_hour_spin); time_layout.addWidget(QLabel(":")); time_layout.addWidget(self.auto_min_spin); layout.addRow("Frequency:", self.auto_freq_combo); layout.addRow("Day of Week:", self.auto_day_combo); layout.addRow("Time (24h):", time_layout); layout.addRow("Include Past Days:", self.auto_days_spin)
        layout.addRow("Recipients:", self.auto_recipients_edit); save = QPushButton("Save Automation Settings"); save.setProperty("cssClass", "primary"); save.clicked.connect(self.save_automation_settings); layout.addRow(save)
        return box

    def create_email_settings_section(self) -> QGroupBox:
        box = QGroupBox("Email Settings"); layout = QFormLayout(box)
        self.email_service_combo = QComboBox(); self.email_service_combo.addItems(["Gmail", "Outlook", "Yahoo", "Custom"]); self.email_edit = QLineEdit(); self.password_edit = QLineEdit(); self.password_edit.setEchoMode(QLineEdit.Password)
        self.smtp_server_edit = QLineEdit(); self.smtp_port_edit = QLineEdit("587")
        try:
            with open('email_settings.json') as f: s = json.load(f)
            self.email_service_combo.setCurrentText(s.get("service", "Gmail")); self.email_edit.setText(s.get("email", "")); self.password_edit.setText(s.get("password", "")); self.smtp_server_edit.setText(s.get("smtp_server", "")); self.smtp_port_edit.setText(str(s.get("smtp_port", 587)))
        except Exception: pass
        layout.addRow("Email Service:", self.email_service_combo); layout.addRow("Email Address:", self.email_edit); layout.addRow("Password / App Password:", self.password_edit); layout.addRow("SMTP Server (for Custom):", self.smtp_server_edit); layout.addRow("SMTP Port:", self.smtp_port_edit)
        btn_layout = QHBoxLayout(); test = QPushButton("Test Connection"); test.setProperty("cssClass", "secondary"); test.clicked.connect(self.test_email_connection); save = QPushButton("Save Email Settings"); save.setProperty("cssClass", "primary"); save.clicked.connect(self.save_email_settings)
        btn_layout.addWidget(test); btn_layout.addWidget(save); layout.addRow(btn_layout)
        return box

    def create_printer_settings_section(self) -> QGroupBox:
        box = QGroupBox("Printer Settings"); layout = QFormLayout(box)
        self.print_system_group = QButtonGroup(self); self.ps_brother_ql_radio = QRadioButton("Brother QL (Direct via brother-ql-next)"); self.ps_cups_radio = QRadioButton("CUPS (System Printing - Linux/macOS)")
        self.print_system_group.addButton(self.ps_brother_ql_radio); self.print_system_group.addButton(self.ps_cups_radio); ps_layout = QHBoxLayout(); ps_layout.addWidget(self.ps_brother_ql_radio); ps_layout.addWidget(self.ps_cups_radio); ps_layout.addStretch(); layout.addRow("Printing System:", ps_layout)
        self.brother_ql_settings_widget = QWidget(); bql_layout = QFormLayout(self.brother_ql_settings_widget); bql_layout.setContentsMargins(0,0,0,0)
        self.conn_usb_radio, self.conn_net_radio = QRadioButton("USB"), QRadioButton("Network"); self.conn_group = QButtonGroup(); self.conn_group.addButton(self.conn_usb_radio); self.conn_group.addButton(self.conn_net_radio)
        conn_layout = QHBoxLayout(); conn_layout.addWidget(self.conn_usb_radio); conn_layout.addWidget(self.conn_net_radio); conn_layout.addStretch(); self.model_combo = QComboBox(); self.model_combo.addItems(["QL-700", "QL-800", "QL-810W", "QL-820NWB", "QL-500", "QL-1100"])
        bql_layout.addRow("Connection Type:", conn_layout); bql_layout.addRow("Printer Model:", self.model_combo)
        scan_layout = QHBoxLayout(); scan_btn = QPushButton("Scan for Printers"); scan_btn.setProperty("cssClass", "accent"); self.discovered_printers_combo = QComboBox(); self.discovered_printers_combo.setPlaceholderText("Select print system then scan...")
        scan_layout.addWidget(self.discovered_printers_combo, 1); scan_layout.addWidget(scan_btn); self.printer_id_edit = QLineEdit(); self.label_combo = QComboBox(); self.label_combo.addItems(["62x29", "62", "62x100", "29x90", "29x42", "17x54"])
        layout.addRow("Discovered Printers:", scan_layout); layout.addRow("Identifier:", self.printer_id_edit); layout.addRow(self.brother_ql_settings_widget); layout.addRow("Label Size:", self.label_combo)
        try:
            with open('printer_settings.json') as f: s = json.load(f)
            if s.get("print_system") == "cups": self.ps_cups_radio.setChecked(True)
            else: self.ps_brother_ql_radio.setChecked(True)
            if s.get("connection_type") == "Network": self.conn_net_radio.setChecked(True)
            else: self.conn_usb_radio.setChecked(True)
            self.printer_id_edit.setText(s.get("printer_identifier", "")); self.model_combo.setCurrentText(s.get("model", "QL-700")); self.label_combo.setCurrentText(s.get("label", "62x29"))
        except Exception: self.ps_brother_ql_radio.setChecked(True); self.conn_usb_radio.setChecked(True)
        btn_layout = QHBoxLayout(); test = QPushButton("Print Test Page"); test.setProperty("cssClass", "secondary"); test.clicked.connect(self.print_test_page_gui); save = QPushButton("Save Printer Settings"); save.setProperty("cssClass", "primary"); save.clicked.connect(self.save_printer_settings)
        btn_layout.addWidget(test); btn_layout.addWidget(save); layout.addRow(btn_layout)
        scan_btn.clicked.connect(self.scan_for_printers); self.discovered_printers_combo.currentIndexChanged.connect(self.on_discovered_printer_selected); self.ps_brother_ql_radio.toggled.connect(self._handle_print_system_changed); self._handle_print_system_changed()
        return box

    @Slot()
    def _handle_print_system_changed(self):
        self.brother_ql_settings_widget.setVisible(self.ps_brother_ql_radio.isChecked())
        self.discovered_printers_combo.clear(); self.printer_id_edit.clear(); self.discovered_printers_combo.setPlaceholderText("Press 'Scan' to find printers...")

    def create_nametag_config_section(self) -> QGroupBox:
        box = QGroupBox("Name Tag Layout Editor"); main_layout = QHBoxLayout(box); controls_widget = QWidget(); controls_layout = QVBoxLayout(controls_widget); main_layout.addWidget(controls_widget, 1)
        for box_name in ['name', 'role']:
            group = QGroupBox(f"{box_name.title()} Box"); form = QFormLayout(group)
            if box_name == 'name':
                name_print_layout = QHBoxLayout(); self.nametag_controls['name_print_first'] = QCheckBox("Print First"); self.nametag_controls['name_print_last'] = QCheckBox("Print Last")
                name_print_layout.addWidget(self.nametag_controls['name_print_first']); name_print_layout.addWidget(self.nametag_controls['name_print_last']); form.addRow(name_print_layout)
            else: self.nametag_controls['role_print_role'] = QCheckBox("Print Role"); form.addRow(self.nametag_controls['role_print_role'])
            for key in ['width', 'height', 'x', 'y']:
                self.nametag_controls[f'{box_name}_{key}'] = QDoubleSpinBox(); self.nametag_controls[f'{box_name}_{key}'].setRange(0, 100); self.nametag_controls[f'{box_name}_{key}'].setSingleStep(0.5)
            self.nametag_controls[f'{box_name}_font'] = QComboBox(); self.nametag_controls[f'{box_name}_bold'] = QCheckBox("Bold"); self.nametag_controls[f'{box_name}_italic'] = QCheckBox("Italic"); self.nametag_controls[f'{box_name}_underline'] = QCheckBox("Underline")
            self.nametag_controls[f'{box_name}_font'].addItems(sorted(["Nunito", "Rounded Mplus 1c", "Aleo", "Arial", "Times New Roman", "Calibri"]))
            style_layout = QHBoxLayout(); style_layout.addWidget(self.nametag_controls[f'{box_name}_bold']); style_layout.addWidget(self.nametag_controls[f'{box_name}_italic']); style_layout.addWidget(self.nametag_controls[f'{box_name}_underline'])
            form.addRow("Width (mm):", self.nametag_controls[f'{box_name}_width']); form.addRow("Height (mm):", self.nametag_controls[f'{box_name}_height']); form.addRow("Center X (mm):", self.nametag_controls[f'{box_name}_x']); form.addRow("Top Y (mm):", self.nametag_controls[f'{box_name}_y'])
            form.addRow("Font:", self.nametag_controls[f'{box_name}_font']); form.addRow("Style:", style_layout)
            for widget in (group.findChildren(QCheckBox) + group.findChildren(QDoubleSpinBox) + group.findChildren(QComboBox)):
                if isinstance(widget, QCheckBox): widget.stateChanged.connect(self.handle_control_change)
                elif isinstance(widget, QDoubleSpinBox): widget.valueChanged.connect(self.handle_control_change)
                elif isinstance(widget, QComboBox): widget.currentTextChanged.connect(self.handle_control_change)
            self.nametag_controls[f'{box_name}_font'].currentTextChanged.connect(lambda font, b=box_name: self.update_font_style_availability(b)); controls_layout.addWidget(group)
        preview_widget = QWidget(); preview_layout = QVBoxLayout(preview_widget); main_layout.addWidget(preview_widget, 1)
        self.preview_label = QLabel("Preview"); self.preview_label.setFixedSize(int(LABEL_WIDTH_MM * 8), int(LABEL_HEIGHT_MM * 8)); self.preview_label.setStyleSheet(f"border: 1px dashed {self.colors['shadow']}; background-color: white;"); self.preview_label.setAlignment(Qt.AlignCenter)
        preview_layout.addWidget(self.preview_label, alignment=Qt.AlignCenter); btn_layout = QHBoxLayout(); save = QPushButton("Save Layout"); save.setProperty("cssClass", "primary"); save.clicked.connect(self.save_nametag_settings)
        load_defaults = QPushButton("Load Defaults"); load_defaults.clicked.connect(self.load_default_nametag_settings_ui); btn_layout.addWidget(save); btn_layout.addWidget(load_defaults); preview_layout.addLayout(btn_layout); self.update_controls_from_box_data()
        return box

    def create_admin_chart(self) -> QWidget:
        widget = QWidget(); widget.setMinimumHeight(400); layout = QVBoxLayout(widget); fig, ax = plt.subplots(); canvas = FigureCanvas(fig); layout.addWidget(canvas)
        self.admin_chart_ax, self.admin_chart_fig, self.admin_chart_canvas = ax, fig, canvas; self.update_admin_chart()
        return widget
        
    def update_active_nav_button(self):
        if self.stack.currentWidget() == self.profile_page: self.profile_btn.setChecked(True)
        else: self.home_btn.setChecked(True)

    def update_dashboard(self):
        today = datetime.now().strftime('%Y-%m-%d')
        
        all_current_records = [r for r in self.attendance.values() if not r.get('is_deleted')]
        
        today_attendance_records = [r for r in all_current_records if r.get('date') == today]
        today_count = len(today_attendance_records)

        visitor_count = sum(1 for entry in today_attendance_records if (mid := next((mid for mid, m in self.members.items() if m['name'] == entry['name']), None)) and "Visitor" in self.members[mid].get("roles", []))
        
        from collections import defaultdict
        counts_by_date = defaultdict(int)
        for record in all_current_records:
            counts_by_date[record['date']] += 1

        totals, count, d = 0, 0, datetime.now()
        for _ in range(4):
            if d.weekday() != 6:
                d -= timedelta(days=d.weekday() + 1)
            
            date_str = d.strftime('%Y-%m-%d')
            if date_str in counts_by_date:
                totals += counts_by_date[date_str]
                count += 1
            d -= timedelta(days=7)

        self.today_attendance_lbl.setText(str(today_count))
        self.new_visitors_lbl.setText(str(visitor_count))
        self.total_members_lbl.setText(str(today_count - visitor_count))
        self.avg_attendance_lbl.setText(str(int(totals/count) if count else 0))
        
        if hasattr(self, 'admin_chart_ax'):
            self.update_admin_chart()
        
    def update_admin_chart(self):
        import matplotlib.dates as mdates
        self.admin_chart_ax.clear()
        
        from collections import defaultdict
        counts_by_date = defaultdict(int)
        for record in self.attendance.values():
            if not record.get('is_deleted'):
                counts_by_date[record['date']] += 1
                
        plot_dates, counts, d = [], [], datetime.now()
        for _ in range(4):
            if d.weekday() != 6:
                d -= timedelta(days=d.weekday() + 1)
            
            plot_dates.append(d)
            counts.append(counts_by_date[d.strftime('%Y-%m-%d')])
            d -= timedelta(days=7)
            
        plot_dates.reverse()
        counts.reverse()
        
        self.admin_chart_ax.plot(plot_dates, counts, color=self.colors['primary'], marker='o')
        self.admin_chart_ax.fill_between(plot_dates, counts, alpha=0.2, color=self.colors['primary'])
        self.admin_chart_ax.set_title("Recent Sunday Attendance")
        self.admin_chart_ax.grid(True, linestyle='--', alpha=0.3)
        self.admin_chart_ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d'))
        for i, c in enumerate(counts):
            self.admin_chart_ax.annotate(str(c), (plot_dates[i], c), textcoords="offset points", xytext=(0,10), ha='center')
            
        self.admin_chart_fig.tight_layout()
        self.admin_chart_canvas.draw()
        
    def populate_profile_list(self):
        self.profile_list.clear(); search_term = self.profile_search_edit.text().lower()
        sorted_members = sorted(self.members.items(), key=lambda item: (item[1].get('last_name', ''), item[1].get('first_name', '')))
        for mid, member in sorted_members:
            if search_term in member['name'].lower():
                item = QListWidgetItem(member['name']); item.setData(Qt.UserRole, mid); self.profile_list.addItem(item)
    
    def display_selected_profile(self):
        if not (items := self.profile_list.selectedItems()):
            self.profile_edit_area.setWidget(QLabel("Select a profile to edit.", alignment=Qt.AlignCenter)); return
        member_id = items[0].data(Qt.UserRole); member = self.members[member_id]; form = ProfileEditForm(member_id, member, self.colors)
        form.saved.connect(self.handle_profile_save); form.deleted.connect(self.handle_profile_delete); self.profile_edit_area.setWidget(form)

    def handle_profile_save(self, member_id, data):
        self.sync_manager.send_update_member(member_id, data)
        QMessageBox.information(self, "Request Sent", "Profile update request sent. Changes will appear shortly.")


    def handle_profile_delete(self, member_id):
        if QMessageBox.question(self, "Confirm", f"Delete profile for {self.members[member_id]['name']}?") == QMessageBox.Yes:
            self.sync_manager.send_delete_member(member_id)
            QMessageBox.information(self, "Request Sent", "Profile deletion request sent. Changes will appear shortly.")
            self.profile_edit_area.setWidget(QLabel("Select a profile to edit.", alignment=Qt.AlignCenter))
    
    def handle_control_change(self):
        self.update_box_from_controls(); self.draw_preview()
            
    def create_name_tag(self, name: str, role: Optional[str]) -> Optional[Image.Image]:
        try:
            img = Image.new('RGB', (696, 271), 'white'); draw = ImageDraw.Draw(img)
            name_text = ""
            if self.boxes['name'].get('print_first', True): name_text += name.split(" ")[0]
            if self.boxes['name'].get('print_last', False):
                if (parts := name.split(" ")) and len(parts) > 1: name_text += f" {parts[-1]}" if name_text else parts[-1]
            text_map = {'name': name_text.strip(), 'role': role if self.boxes['role'].get('print_role', True) and role else ""}
            for box_name, text in text_map.items():
                if not text: continue
                box = self.boxes[box_name]; x, y, w, h = box['x']*SCALE_300DPI, box['y']*SCALE_300DPI, box['width']*SCALE_300DPI, box['height']*SCALE_300DPI
                style = "Bold Italic" if box['bold'] and box['italic'] else "Bold" if box['bold'] else "Italic" if box['italic'] else "Regular"
                font_path = self.app_fonts.get(box['font_family'], {}).get(style) or self.app_fonts.get(box['font_family'], {}).get("Regular")
                if not font_path: pil_font = ImageFont.load_default()
                else:
                    font_size = int(h * 0.9)
                    pil_font = ImageFont.truetype(font_path, font_size)
                    while pil_font.getbbox(text)[2] > w and font_size > 4:
                        font_size -= 1; pil_font = ImageFont.truetype(font_path, font_size)
                bbox = draw.textbbox((0,0), text, font=pil_font); tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
                tx, ty = x-(tw/2), y+(h-th)/2-bbox[1]; draw.text((tx, ty), text, font=pil_font, fill="black")
                if box.get('underline', False): draw.line([(tx, ty+th+2), (tx+tw, ty+th+2)], fill='black', width=max(1, int(font_size*0.05)))
            return img
        except Exception as e:
            logging.error(f"Failed to create name tag image: {e}", exc_info=True)
            QMessageBox.critical(self, "Image Error", f"Failed to create name tag image: {e}"); return None
    
    def draw_preview(self):
        pixmap = QPixmap(self.preview_label.size()); pixmap.fill(Qt.white); painter = QPainter(pixmap); painter.setRenderHint(QPainter.Antialiasing)
        name_text = ("John" if self.nametag_controls['name_print_first'].isChecked() else "") + (" Smith" if self.nametag_controls['name_print_last'].isChecked() else "")
        text_map = {'name': name_text.strip(), 'role': "Pastor" if self.nametag_controls['role_print_role'].isChecked() else ""}
        for box_name, text in text_map.items():
            if not text: continue
            box = self.boxes[box_name]; sx, sy = self.preview_label.width()/LABEL_WIDTH_MM, self.preview_label.height()/LABEL_HEIGHT_MM
            w, h, x, y = box['width']*sx, box['height']*sy, box['x']*sx, box['y']*sy; rect = QRect(int(x-w/2), int(y), int(w), int(h))
            style = "Bold Italic" if box['bold'] and box['italic'] else "Bold" if box['bold'] else "Italic" if box['italic'] else "Regular"
            font_path = self.app_fonts.get(box['font_family'], {}).get(style) or self.app_fonts.get(box['font_family'], {}).get("Regular")
            qfont = QFont()
            if font_path:
                if (families := QFontDatabase.applicationFontFamilies(id := QFontDatabase.addApplicationFont(font_path))): qfont.setFamily(families[0])
                QFontDatabase.removeApplicationFont(id)
            else: qfont.setFamily(box['font_family'])
            qfont.setBold(box['bold']); qfont.setItalic(box['italic']); font_size = int(h*0.9); qfont.setPixelSize(font_size); metrics = QFontMetrics(qfont)
            while metrics.horizontalAdvance(text) > rect.width() and font_size > 4: font_size -= 1; qfont.setPixelSize(font_size); metrics = QFontMetrics(qfont)
            painter.setPen(QColor(self.colors['text'])); painter.setFont(qfont); painter.drawText(rect, Qt.AlignCenter, text)
            if box.get('underline', False):
                bbox = metrics.boundingRect(rect, Qt.AlignCenter, text); underline_y = bbox.bottom() + 2; pen = painter.pen(); pen.setWidth(max(1, int(font_size*0.05))); painter.setPen(pen)
                painter.drawLine(bbox.left(), underline_y, bbox.right(), underline_y)
        painter.end(); self.preview_label.setPixmap(pixmap)

    def update_box_from_controls(self):
        for box_name in ['name', 'role']:
            for key in ['width', 'height', 'x', 'y']: self.boxes[box_name][key] = self.nametag_controls[f'{box_name}_{key}'].value()
            for key in ['bold', 'italic', 'underline']: self.boxes[box_name][key] = self.nametag_controls[f'{box_name}_{key}'].isChecked()
            self.boxes[box_name]['font_family'] = self.nametag_controls[f'{box_name}_font'].currentText()
            if box_name == 'name': self.boxes['name']['print_first'], self.boxes['name']['print_last'] = self.nametag_controls['name_print_first'].isChecked(), self.nametag_controls['name_print_last'].isChecked()
            if box_name == 'role': self.boxes['role']['print_role'] = self.nametag_controls['role_print_role'].isChecked()

    def update_controls_from_box_data(self):
        for control in self.nametag_controls.values(): control.blockSignals(True)
        try:
            for box_name in ['name', 'role']:
                for key in ['width', 'height', 'x', 'y']: self.nametag_controls[f'{box_name}_{key}'].setValue(self.boxes[box_name].get(key, 0))
                for key in ['bold', 'italic', 'underline']: self.nametag_controls[f'{box_name}_{key}'].setChecked(self.boxes[box_name].get(key, False))
                self.nametag_controls[f'{box_name}_font'].setCurrentText(self.boxes[box_name].get('font_family', 'Arial'))
                if box_name == 'name': self.nametag_controls['name_print_first'].setChecked(self.boxes['name'].get('print_first', True)); self.nametag_controls['name_print_last'].setChecked(self.boxes['name'].get('print_last', False))
                if box_name == 'role': self.nametag_controls['role_print_role'].setChecked(self.boxes['role'].get('print_role', True))
        finally:
            for control in self.nametag_controls.values(): control.blockSignals(False)
        self.draw_preview()
        
    def update_font_style_availability(self, box_name: str):
        italic_checkbox = self.nametag_controls[f'{box_name}_italic']
        if self.nametag_controls[f'{box_name}_font'].currentText() in ["Rounded Mplus 1c"]:
            if italic_checkbox.isChecked(): italic_checkbox.setChecked(False) 
            italic_checkbox.setDisabled(True)
        else: italic_checkbox.setDisabled(False)

    def get_default_nametag_settings(self) -> dict:
        return {
            'name': {'width':60, 'height':12, 'x':31, 'y':7, 'font_family':"Rounded Mplus 1c", 'bold':True, 'italic':False, 'underline':False, 'print_first':True, 'print_last':False},
            'role': {'width':40, 'height':8, 'x':31, 'y':18, 'font_family':"Nunito", 'bold':False, 'italic':True, 'underline':False, 'print_role':True}
        }

    def load_default_nametag_settings_ui(self):
        self.boxes = self.get_default_nametag_settings(); self.update_controls_from_box_data()
        QMessageBox.information(self, "Defaults Loaded", "Default name tag layout has been loaded.")

    def record_attendance(self, name: str) -> bool:
        today_str = datetime.now().strftime('%Y-%m-%d')
        now_time = datetime.now()

        todays_checkins = [
            rec for rec in self.attendance.values()
            if rec.get('date') == today_str and not rec.get('is_deleted')
        ]

        if any(entry['name'] == name for entry in todays_checkins):
            QMessageBox.information(self, "Already Checked In", f"{name} is already checked in.")
            return False
        
        self.sync_manager.send_checkin(name=name, date=today_str, time_str=now_time.strftime('%H:%M:%S'))
        
        QMessageBox.information(self, "Check-in Sent", f"{name}'s check-in has been sent to the server. The list will update shortly.")
        return True

    def print_name_tag(self, name: str, roles: List[str]):
        try:
            logging.info(f"Creating name tag for '{name}'.")
            if not (img_object := self.create_name_tag(name, roles[0] if roles else None)):
                logging.error("Name tag image object was not created."); return
            nametags_dir = Path("nametags"); nametags_dir.mkdir(exist_ok=True)
            safe_name = "".join(c for c in name if c.isalnum())
            backup_path = nametags_dir / f"nametag_{safe_name}_{datetime.now():%Y%m%d%H%M%S}.png"
            img_object.save(backup_path)
            logging.info(f"Name tag image saved to backup: {backup_path}")
            printer = PrinterInterface(
                model=self.model_combo.currentText(), printer_identifier=self.printer_id_edit.text(),
                backend='network' if self.conn_net_radio.isChecked() else 'pyusb',
                print_system='cups' if self.ps_cups_radio.isChecked() else 'brother-ql'
            )
            success, message = printer.print_label(image_path=str(backup_path), label_type=self.label_combo.currentText())
            if success:
                logging.info(f"Successfully sent name tag for {name} to printer.")
                try: os.unlink(backup_path)
                except Exception as e: logging.warning(f"Could not remove backup nametag {backup_path}: {e}")
            else:
                QMessageBox.warning(self, "Print Failed", f"Could not print name tag for {name}.\nReason: {message}\nImage saved to '{backup_path}' for manual printing.")
        except Exception as e:
            logging.error(f"Error in print_name_tag for {name}: {e}", exc_info=True)
            QMessageBox.critical(self, "Print Error", f"An unexpected error occurred while printing the name tag for {name}:\n{e}")
            
    def update_checkin_lists(self):
        self.update_all_members_list(); self.update_checked_in_list_display(); self.update_quick_select_buttons()

    def update_all_members_list(self):
        self.all_members_list.clear(); term = self.checkin_search_edit.text().lower()
        matches = sorted([m for m in self.members.values() if term in m['name'].lower()], key=lambda m: (not m['name'].lower().startswith(term), m['name'].lower()))
        for member in matches:
            if mid := next((k for k,v in self.members.items() if v==member), None):
                item = QListWidgetItem(member['name']); item.setData(Qt.UserRole, mid); self.all_members_list.addItem(item)
            
    def update_checked_in_list_display(self):
        self.checked_in_list.clear()
        today = datetime.now().strftime('%Y-%m-%d')

        todays_checkins = [
            rec for rec in self.attendance.values()
            if rec.get('date') == today and not rec.get('is_deleted')
        ]
        
        for entry in sorted(todays_checkins, key=lambda x: x['name']):
            try:
                checkin_time = datetime.strptime(f"{today} {entry['time']}", '%Y-%m-%d %H:%M:%S')
                display_text = f"{entry['name']} (at {checkin_time:%I:%M %p})"
                item = QListWidgetItem(display_text)
                
                item.setData(Qt.UserRole, entry['record_id'])
                
                self.checked_in_list.addItem(item)
            except ValueError:
                continue
            
    def update_quick_select_buttons(self):
        for i, btn in enumerate(self.quick_select_buttons):
            if i < self.all_members_list.count():
                item = self.all_members_list.item(i); member = self.members[item.data(Qt.UserRole)]; btn.setText(member['name']); btn.setVisible(True)
                try: btn.clicked.disconnect()
                except RuntimeError: pass
                btn.clicked.connect(lambda _, m=member: self.handle_quick_select(m))
            else: btn.setVisible(False)

    def handle_quick_select(self, member):
        if QMessageBox.question(self, "Confirm Check-in", f"Check in as {member['name']}?") == QMessageBox.Yes:
            if self.record_attendance(member['name']): self.print_name_tag(member['name'], member.get('roles', []))
            self.checkin_search_edit.clear()

    def handle_checkin_selected(self):
        if not self.all_members_list.selectedItems(): return QMessageBox.warning(self, "Selection Required", "Please select a name from the 'All Members' list.")
        item = self.all_members_list.selectedItems()[0]; self.handle_quick_select(self.members[item.data(Qt.UserRole)])

    def handle_remove_checkin(self):
        if not self.checked_in_list.selectedItems():
            return QMessageBox.warning(self, "Selection Required", "Select a name from the 'Currently Checked In' list to remove.")
        
        selected_item = self.checked_in_list.selectedItems()[0]
        record_id_to_remove = selected_item.data(Qt.UserRole)
        name_to_remove = self.attendance.get(record_id_to_remove, {}).get('name', 'this person')

        if QMessageBox.question(self, "Confirm Removal", f"Remove check-in for {name_to_remove}?") == QMessageBox.Yes:
            self.sync_manager.send_tombstone_attendance(record_id=record_id_to_remove)
            QMessageBox.information(self, "Request Sent", f"Removal request for {name_to_remove} sent. The list will update shortly.")
            
    def handle_registration(self, check_in=False):
        first = self.new_first_name_edit.text().strip()
        last = self.new_last_name_edit.text().strip()
        if not first or not last:
            return QMessageBox.warning(self, "Input Error", "First and Last Name are required.")
        
        name = f"{first} {last}"
        
        if any(m['name'].lower() == name.lower() for m in self.members.values()):
            return QMessageBox.warning(self, "Already Exists", "This name is already registered locally.")
        
        new_member_data = {
            'id': str(uuid.uuid4()), 
            'name': name,
            'first_name': first,
            'last_name': last,
            'roles': [self.new_role_combo.currentText()],
            'first_visit': datetime.now().strftime('%Y-%m-%d')
        }
        
        self.sync_manager.send_new_member(new_member_data)
        
        message = f"Welcome, {name}! Registration request sent."
        if check_in:
            self.record_attendance(name)
            self.print_name_tag(name, new_member_data['roles'])
        
        QMessageBox.information(self, "Success", message)
        self.new_first_name_edit.clear()
        self.new_last_name_edit.clear()
        self.stack.setCurrentWidget(self.home_page)

    def attempt_auto_reconnect(self):
        logging.info("Attempting auto-reconnect to printer...")
        try:
            with open('printer_settings.json') as f: s = json.load(f)
            if s.get("print_system") == "cups": logging.info("Auto-reconnect skipped; CUPS is selected."); return
            if not s.get('printer_identifier'): return
        except (FileNotFoundError, json.JSONDecodeError): return
        if not (res := PrinterInterface().discover_printer())[0]: return
        if match := next((p for p in res[1] if p['identifier'] == s['printer_identifier']), None):
            logging.info(f"Printer auto-reconnect successful: {match['raw_identifier']}"); self.printer_id_edit.setText(match['identifier'])
            self.model_combo.setCurrentText(match.get('model_guess', s['model'])); self.conn_net_radio.setChecked(match.get('backend') == 'network')
        else: logging.warning("Auto-reconnect failed: Previously saved printer not found.")
            
    def scan_for_printers(self):
        self.discovered_printers_combo.clear()
        self.discovered_printers_combo.addItem("Scanning...")
        QApplication.processEvents()

        self._perform_printer_scan()

        self.discovered_printers_combo.clear()
        if self.discovered_printers:
            self.discovered_printers_combo.addItem("Select a printer...")
            for p in self.discovered_printers:
                self.discovered_printers_combo.addItem(p['raw_identifier'])
        else:
            QMessageBox.information(self, "Scan Complete", "No printers found.")
            self.discovered_printers_combo.addItem("No printers found")

    def on_discovered_printer_selected(self, index: int):
        if index > 0 and hasattr(self, 'discovered_printers') and self.discovered_printers:
            p = self.discovered_printers[index - 1]; self.printer_id_edit.setText(p['identifier']); self.model_combo.setCurrentText(p['model_guess'])
            self.conn_net_radio.setChecked(p['backend'] == 'network')

    def print_test_page_gui(self):
        width_px, height_px = 696, 271
        img = Image.new('RGB', (width_px, height_px), 'white')
        draw = ImageDraw.Draw(img)
        try: font = ImageFont.truetype("Arial.ttf", 60)
        except IOError: font = ImageFont.load_default()
        text = "Test Print"; bbox = draw.textbbox((0, 0), text, font=font)
        pos = ((width_px - (bbox[2]-bbox[0]))/2, (height_px - (bbox[3]-bbox[1]))/2)
        draw.text(pos, text, fill='black', font=font)
        draw.rectangle((0,0,width_px-1,height_px-1), outline='black', width=5)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as temp_f:
            img.save(temp_f.name)
            printer = PrinterInterface(
                model=self.model_combo.currentText(), printer_identifier=self.printer_id_edit.text(), 
                backend='network' if self.conn_net_radio.isChecked() else 'pyusb',
                print_system='cups' if self.ps_cups_radio.isChecked() else 'brother-ql'
            )
            success, message = printer.print_label(image_path=temp_f.name, label_type=self.label_combo.currentText())
            if success: QMessageBox.information(self, "Success", "Test print sent successfully.")
            else: QMessageBox.critical(self, "Print Error", f"Test print failed: {message}")
            os.unlink(temp_f.name)

    def save_printer_settings(self):
        if self.ps_cups_radio.isChecked():
            backend = 'cups'
        elif self.conn_net_radio.isChecked():
            backend = 'network'
        else:
            backend = 'pyusb'
            
        raw_identifier = self.printer_id_edit.text().strip()
        if found := next((p for p in self.discovered_printers if p['identifier'] == raw_identifier), None):
            raw_identifier = found.get('raw_identifier', raw_identifier)

        settings = {
            'print_system': 'cups' if self.ps_cups_radio.isChecked() else 'brother-ql',
            'backend': backend,
            'connection_type': 'Network' if self.conn_net_radio.isChecked() else 'USB',
            'printer_identifier': self.printer_id_edit.text().strip(), 
            'raw_identifier': raw_identifier,
            'model': self.model_combo.currentText(), 
            'label': self.label_combo.currentText()
        }
        self.save_data('printer_settings.json', settings)
        
        if isinstance(self.sender(), QPushButton):
            QMessageBox.information(self, "Saved", "Printer settings saved.")

    def generate_report(self, start_str, end_str, send_email, recipients=None):
        try:
            import pandas as pd
            start_dt = datetime.strptime(start_str, '%Y-%m-%d').date()
            end_dt = datetime.strptime(end_str, '%Y-%m-%d').date()
            
            report_data = []
            for record in self.attendance.values():
                if record.get('is_deleted'):
                    continue
                
                try:
                    record_date = datetime.strptime(record['date'], '%Y-%m-%d').date()
                    if start_dt <= record_date <= end_dt:
                        report_data.append({
                            'Name': record['name'],
                            'Date': record['date'],
                            'Time': record['time']
                        })
                except (ValueError, TypeError):
                    continue

            if not report_data:
                msg = "No attendance data found for the selected period."
                if not send_email:
                    QMessageBox.warning(self, "No Data", msg)
                return (True, msg)
            
            df = pd.DataFrame(report_data).sort_values(['Date', 'Time'])
            reports_dir = Path("reports")
            reports_dir.mkdir(exist_ok=True)
            path = reports_dir / f"attendance_report_{start_str}_to_{end_str}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
            df.to_excel(path, index=False)
            
            if send_email:
                return self.email_report(str(path), [r.strip() for r in recipients.split(',')])
                
            message = f"Report saved successfully to:\n{path}"
            QMessageBox.information(self, "Report Generated", message)
            return (True, message)
            
        except Exception as e:
            error_message = f"An unexpected error occurred while generating the report: {e}"
            logging.error(error_message, exc_info=True)
            if not send_email:
                QMessageBox.critical(self, "Report Error", error_message)
            return (False, str(e))
            
    def email_report(self, filename, recipients):
        try:
            import smtplib; from email.mime.text import MIMEText; from email.mime.multipart import MIMEMultipart; from email.mime.application import MIMEApplication
            with open('email_settings.json') as f: s = json.load(f)
            msg = MIMEMultipart(); msg['Subject'] = f"Attendance Report: {Path(filename).name}"; msg['From'] = s['email']; msg['To'] = ', '.join(recipients)
            msg.attach(MIMEText("Please find the attached attendance report.", 'plain'))
            with open(filename, "rb") as f:
                part = MIMEApplication(f.read(), Name=Path(filename).name); part['Content-Disposition'] = f'attachment; filename="{Path(filename).name}"'; msg.attach(part)
            server = smtplib.SMTP(s['smtp_server'], int(s['smtp_port'])); server.starttls(); server.login(s['email'], s['password']); server.send_message(msg); server.quit()
            return (True, f"Report successfully emailed to {', '.join(recipients)}")
        except Exception as e: return (False, str(e))
            
    def save_automation_settings(self):
        settings = {'frequency':self.auto_freq_combo.currentText(),'day':self.auto_day_combo.currentText(),'hour':self.auto_hour_spin.value(),'minute':self.auto_min_spin.value(),'days_to_include':self.auto_days_spin.value(),'recipients':[r.strip() for r in self.auto_recipients_edit.toPlainText().split(',') if r.strip()],'last_run':None}
        self.save_data('report_automation.json', settings); QMessageBox.information(self, "Saved", "Automation settings saved."); self.schedule_automated_reports()

    def run_automated_report_job(self):
        logging.info("Running automated report job...")
        try:
            with open('report_automation.json') as f: s = json.load(f)
            end_date = datetime.now(); start_date = end_date - timedelta(days=s.get('days_to_include', 7))
            success, message = self.generate_report(start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'), True, ','.join(s.get('recipients', [])))
            self.reportJobFinished.emit(success, message)
        except Exception as e:
            logging.error(f"Failed to run automated report job: {e}", exc_info=True); self.reportJobFinished.emit(False, str(e))

    def save_email_settings(self):
        settings = {'service': self.email_service_combo.currentText(),'email': self.email_edit.text(),'password': self.password_edit.text(),'smtp_server': self.smtp_server_edit.text(),'smtp_port': self.smtp_port_edit.text()}
        self.save_data('email_settings.json', settings); QMessageBox.information(self, "Saved", "Email settings saved.")
        
    def test_email_connection(self):
        try:
            import smtplib
            s = {'service':self.email_service_combo.currentText(),'email':self.email_edit.text(),'password':self.password_edit.text(),'smtp_server':self.smtp_server_edit.text(),'smtp_port':int(self.smtp_port_edit.text())}
            with smtplib.SMTP(s['smtp_server'], s['smtp_port'], timeout=10) as server: server.starttls(); server.login(s['email'], s['password'])
            QMessageBox.information(self, "Success", "Email connection test was successful!")
        except Exception as e: QMessageBox.critical(self, "Connection Failed", f"Email connection test failed:\n{e}")

    def schedule_automated_reports(self):
        schedule.clear()
        try:
            with open('report_automation.json') as f: s = json.load(f)
            job = self.run_automated_report_job; time_str = f"{s['hour']:02d}:{s['minute']:02d}"
            if (freq := s['frequency']) == "Daily": schedule.every().day.at(time_str).do(job)
            elif freq == "Weekly": getattr(schedule.every(), s['day'].lower()).at(time_str).do(job)
            logging.info(f"Scheduled automated report with frequency: {freq}")
        except Exception as e: logging.error(f"Failed to schedule reports: {e}")
            
    def start_scheduler_thread(self):
        class ScheduleWorker(QObject):
            def __init__(self): super().__init__(); self.running = True
            def run(self):
                while self.running: schedule.run_pending(); time.sleep(1)
        self.worker_thread = QThread(); self.worker = ScheduleWorker(); self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run); self.worker_thread.start()

    def closeEvent(self, event: QCloseEvent):
        logging.info("Close event triggered. Shutting down...")
        
        if hasattr(self, 'db'):
            self.db.close()
            
        if hasattr(self, 'sync_manager'):
            self.sync_manager.stop()
            
        if hasattr(self, 'worker'):
            self.worker.running = False
            
        if hasattr(self, 'worker_thread'):
            self.worker_thread.quit()
            self.worker_thread.wait(3000)
            
        event.accept()

class PrinterSelectionDialog(QDialog):
    printer_selected = Signal(dict)

    def __init__(self, printers: List[dict], current_printer_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Printer Connection Status")
        self.setMinimumWidth(500)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        if current_printer_name:
            status_text = f"✅ Currently connected to: <b>{current_printer_name}</b>"
            prompt_text = "You can switch to another available printer below:"
        else:
            status_text = "❌ No printer is currently connected."
            prompt_text = "Please select an available printer:"
            
        layout.addWidget(QLabel(status_text, styleSheet="font-size: 13pt;"))
        layout.addWidget(QLabel(prompt_text))

        scroll_area = QScrollArea(); scroll_area.setWidgetResizable(True)
        scroll_content = QWidget(); button_layout = QVBoxLayout(scroll_content)
        button_layout.setContentsMargins(0, 5, 0, 5); button_layout.setSpacing(8)
        scroll_area.setWidget(scroll_content)
        layout.addWidget(scroll_area)

        if not printers:
            button_layout.addWidget(QLabel("No printers were found."))
        else:
            button_style = """
                QPushButton {
                    text-align: left;
                    padding: 12px;
                    border: 1px solid #ccc;
                    border-radius: 4px;
                    background-color: #f8f9fa;
                }
                QPushButton:hover {
                    background-color: #e9ecef;
                    border-color: #0076c6;
                }
            """
            status_colors = {
                'Connected': '#2e7d32',
                'Available': '#0a3d7d',
                'Offline': '#616161'
            }

            for p_info in printers:
                status = p_info.get('status', 'Offline')
                name = p_info.get('raw_identifier', p_info.get('identifier', 'Unknown'))
                
                button = QPushButton(name)
                button.setStyleSheet(button_style)
                button.clicked.connect(lambda _, p=p_info: self.on_printer_chosen(p))
                
                status_label = QLabel(f"[{status}]")
                status_label.setStyleSheet(f"color: {status_colors.get(status, '#000')}; font-weight: bold;")
                
                row = QHBoxLayout()
                row.addWidget(button, 1)
                row.addWidget(status_label)
                button_layout.addLayout(row)
        
        button_layout.addStretch()

        cancel_button = QPushButton("Cancel"); cancel_button.clicked.connect(self.reject)
        layout.addWidget(cancel_button, alignment=Qt.AlignRight)

    def on_printer_chosen(self, printer_info: dict):
        self.printer_selected.emit(printer_info)
        self.accept()

class ProfileEditForm(QWidget):
    saved = Signal(str, dict); deleted = Signal(str)
    def __init__(self, member_id, member_data, colors, parent=None):
        super().__init__(parent); self.member_id = member_id; self.colors = colors; self.init_ui(member_data)

    def init_ui(self, member):
        layout = QVBoxLayout(self); layout.setContentsMargins(20,20,20,20); form_layout = QFormLayout()
        self.first_edit, self.last_edit = QLineEdit(member.get('first_name')), QLineEdit(member.get('last_name'))
        self.role_combo = QComboBox(); self.role_combo.addItems(["Visitor","Regular Attendee","Congregation Member","Sunday School Teacher","TYF Leader","PLUS Leader","Small Group Leader","Committee Member","Diaconate Member","Elder","Pastor"])
        self.role_combo.setCurrentText(member.get('roles', ['Visitor'])[0])
        form_layout.addRow("First Name:", self.first_edit); form_layout.addRow("Last Name:", self.last_edit); form_layout.addRow("Church Role:", self.role_combo)
        layout.addLayout(form_layout); layout.addStretch(); btn_layout = QHBoxLayout(); save_btn = QPushButton("Save Changes"); save_btn.setProperty("cssClass", "primary")
        del_btn = QPushButton("Delete Profile"); del_btn.setStyleSheet(f"background-color: #dc3545; color: white;"); btn_layout.addWidget(save_btn); btn_layout.addWidget(del_btn)
        layout.addLayout(btn_layout); save_btn.clicked.connect(self.on_save); del_btn.clicked.connect(lambda: self.deleted.emit(self.member_id))
    
    def on_save(self):
        if not (first := self.first_edit.text().strip()) or not (last := self.last_edit.text().strip()): return QMessageBox.warning(self, "Error", "First and Last name are required.")
        self.saved.emit(self.member_id, {'first_name':first,'last_name':last,'name':f"{first} {last}",'roles':[self.role_combo.currentText()]})

def load_application_fonts(main_window: QMainWindow):
    main_window.app_fonts = {}
    font_dir = Path(__file__).parent / "fonts"
    if not font_dir.is_dir():
        logging.warning("Fonts directory not found. Skipping custom font loading.")
        return

    logging.info("--- Building Precise Font Map ---")
    db = QFontDatabase()

    font_files = list(font_dir.glob('*.ttf')) + list(font_dir.glob('*.otf'))

    for font_file in font_files:
        font_id = db.addApplicationFont(str(font_file))
        if font_id != -1:
            for family in db.applicationFontFamilies(font_id):
                font_info = QFontInfo(db.font(family, "", -1))
                style = font_info.styleName()
                base_family = family

                for keyword in ["Bold", "Italic", "Black", "Medium", "Light", "Thin", "Regular"]:
                    if family.endswith(f" {keyword}"):
                        base_family = family.removesuffix(f" {keyword}").strip()
                        if style in ["Normal", "Regular"]:
                           style = keyword
                        break
                
                if base_family not in main_window.app_fonts:
                    main_window.app_fonts[base_family] = {}
                
                main_window.app_fonts[base_family][style] = str(font_file)
                logging.info(f"Mapped: '{base_family}' -> '{style}' -> {font_file.name}")
            db.removeApplicationFont(font_id)

    unique_font_paths = {path for fam_dict in main_window.app_fonts.values() for path in fam_dict.values()}
    for path in unique_font_paths:
        QFontDatabase.addApplicationFont(path)
    
    logging.info("--- Font Map Complete ---")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    db_manager = DatabaseManager()
    migrate_from_json_to_sqlite(db_manager)
    main_window = MainWindow(db_manager=db_manager) 
    load_application_fonts(main_window)
    app.setFont(QFont("Nunito", 11))
    splash = SplashScreen(); splash.show()
    QTimer.singleShot(1500, lambda: (splash.close(), main_window.show()))
    sys.exit(app.exec())
