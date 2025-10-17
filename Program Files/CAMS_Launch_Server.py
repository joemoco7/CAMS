#!/usr/bin/env python3
import os
import subprocess
import sys

# Get the absolute path of the directory containing this launcher script
# This makes the script runnable from any location
script_directory = os.path.dirname(os.path.abspath(__file__))

# Construct the paths to the virtual environment's Python interpreter and the main script
venv_python_path = os.path.join(script_directory, "venv", "bin", "python")
main_script_path = os.path.join(script_directory, "CAMS_Server.py")

# Check if the virtual environment's Python executable exists
if not os.path.exists(venv_python_path):
    print(f"Error: The virtual environment's Python interpreter was not found at {venv_python_path}")
    print("Please ensure the virtual environment is named 'venv' and is in the same directory.")
    sys.exit(1)

# Check if the main Python script exists
if not os.path.exists(main_script_path):
    print(f"Error: The main script 'CAMS_Server.py' was not found at {main_script_path}")
    sys.exit(1)

# Execute the main script using the virtual environment's Python interpreter
try:
    subprocess.run([venv_python_path, main_script_path], check=True)
except subprocess.CalledProcessError as e:
    print(f"An error occurred while running the main script: {e}")
    sys.exit(1)
except KeyboardInterrupt:
    print("\nScript execution interrupted by user.")
    sys.exit(0)
