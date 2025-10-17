import json
import logging
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request
from zeroconf import ServiceInfo, Zeroconf
from wsgiref.simple_server import make_server

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- CONFIGURATION ---
SERVER_PORT = 5678
SERVICE_TYPE = "_attendance-sync._tcp.local."
DATA_DIR = Path(".") # Store data files in the same directory as the script

# --- Server State ---
server_attendance_data = {}
server_members_data = {}
server_attendance_file = DATA_DIR / "server_master_attendance.json"
server_members_file = DATA_DIR / "server_master_members.json"

def load_server_files():
    """Load data from JSON files into memory."""
    global server_attendance_data, server_members_data
    try:
        with open(server_attendance_file, 'r', encoding='utf-8') as f:
            server_attendance_data = json.load(f)
        logging.info(f"Loaded {len(server_attendance_data)} attendance records.")
    except (FileNotFoundError, json.JSONDecodeError):
        server_attendance_data = {}
        logging.info("No attendance data file found. Starting fresh.")
    try:
        with open(server_members_file, 'r', encoding='utf-8') as f:
            server_members_data = json.load(f)
        logging.info(f"Loaded {len(server_members_data)} member records.")
    except (FileNotFoundError, json.JSONDecodeError):
        server_members_data = {}
        logging.info("No members data file found. Starting fresh.")

def save_server_files():
    """Save in-memory data to JSON files."""
    try:
        with open(server_attendance_file, 'w', encoding='utf-8') as f:
            json.dump(server_attendance_data, f, indent=4)
        with open(server_members_file, 'w', encoding='utf-8') as f:
            json.dump(server_members_data, f, indent=4)
    except Exception as e:
        logging.error(f"Error saving master files: {e}")

# --- Flask App and API Endpoints ---
app = Flask(__name__)

@app.route('/ping', methods=['GET'])
def ping():
    return "pong", 200

@app.route('/get_all_data', methods=['GET'])
def get_all_data():
    return jsonify({
        "attendance": server_attendance_data,
        "members": server_members_data
    })

@app.route('/get_updates_since', methods=['GET'])
def get_updates_since():
    client_timestamp_str = request.args.get('timestamp', '1970-01-01T00:00:00.000000+00:00')
    
    updated_members = {
        mid: mdata for mid, mdata in server_members_data.items()
        
        # --- THIS IS THE LINE TO CHANGE ---
        if (mdata.get('last_modified_utc') or '') > client_timestamp_str
        # ---
    }
    # For attendance, we can use a similar approach if we add a timestamp
    # For now, we'll send all non-deleted attendance as a safe default
    updated_attendance = {
        rid: rdata for rid, rdata in server_attendance_data.items()
    }
    
    return jsonify({
        "attendance": updated_attendance,
        "members": updated_members,
        "server_time": datetime.now(timezone.utc).isoformat()
    })


@app.route('/batch_update', methods=['POST'])
def batch_update():
    """A single, powerful endpoint to receive all changes from a client."""
    data = request.json
    client_program_id = data.get('program_id', 'unknown_client')
    
    # Process member updates
    for member_data in data.get('members', []):
        member_id = member_data.get('id')
        if not member_id: continue

        # Last Write Wins: only update if the incoming data is newer
        server_record = server_members_data.get(member_id)
        if not server_record or member_data.get('last_modified_utc') > server_record.get('last_modified_utc', ''):
            logging.info(f"Updating member {member_id} from {client_program_id}")
            member_data['last_modified_by'] = client_program_id
            server_members_data[member_id] = member_data

    # Process attendance updates
    for att_data in data.get('attendance', []):
        record_id = att_data.get('record_id')
        if not record_id: continue
        
        server_record = server_attendance_data.get(record_id)
        # Handle deletions as final
        if att_data.get('is_deleted') and server_record:
            logging.info(f"Marking attendance record {record_id} as deleted from {client_program_id}")
            server_record.update(att_data)
        elif not server_record:
             server_attendance_data[record_id] = att_data

    save_server_files()
    return jsonify({"status": "success"}), 200

# --- Server Utilities ---
def get_local_ip() -> str:
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

def start_zeroconf(ip, port):
    """Broadcast the server on the local network."""
    info = ServiceInfo(
        SERVICE_TYPE,
        f"AttendanceServer-{ip.replace('.', '-')}.{SERVICE_TYPE}",
        addresses=[socket.inet_aton(ip)],
        port=port,
        properties={'desc': 'Church Attendance Sync Server'}
    )
    zeroconf = Zeroconf()
    logging.info("Registering Zeroconf service...")
    zeroconf.register_service(info)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Unregistering service...")
        zeroconf.unregister_service(info)
        zeroconf.close()

if __name__ == "__main__":
    load_server_files()
    
    server_ip = get_local_ip()
    
    # Start Zeroconf in a separate thread
    zeroconf_thread = threading.Thread(target=start_zeroconf, args=(server_ip, SERVER_PORT), daemon=True)
    zeroconf_thread.start()
    
    logging.info(f"Starting Flask server on http://{server_ip}:{SERVER_PORT}")
    try:
        server = make_server('0.0.0.0', SERVER_PORT, app)
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down server.")
        server.shutdown()