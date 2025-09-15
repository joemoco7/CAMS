import sys
import subprocess
import os
import venv

def check_system_dependencies():
    """
    Checks for required system-level dependencies on Ubuntu.
    """
    print("Checking for required system dependencies...")
    missing_packages = []
    
    # Dependencies needed for PySide6, Pillow, and pyusb
    required_apt_packages = {
        "libGL.so.1": "libgl1",  # For Qt via PySide6  # For Qt via PySide6
        "libusb-1.0.so.0": "libusb-1.0-0",  # For pyusb
        "libjpeg.so.8": "libjpeg-turbo8", # For Pillow (JPEG support)
        "libfreetype.so.6": "libfreetype6" # For Pillow (TrueType font support)
    }

    for lib, package_name in required_apt_packages.items():
        # Using 'ldconfig -p' is a reliable way to check for shared libraries
        try:
            subprocess.check_output(['ldconfig', '-p'], text=True).find(lib)
        except (subprocess.CalledProcessError, FileNotFoundError):
            # If ldconfig fails, fall back to checking dpkg
            try:
                result = subprocess.run(['dpkg', '-s', package_name], capture_output=True, text=True)
                if "Status: install ok installed" not in result.stdout:
                    missing_packages.append(package_name)
            except FileNotFoundError:
                 # dpkg not found, this is highly unlikely on Ubuntu but good to handle
                 print(f"Warning: 'dpkg' not found. Cannot verify system dependency: {package_name}")
                 continue # Continue without adding to missing packages
        else:
            # ldconfig check was successful, now let's see if the library is found
            if subprocess.check_output(['ldconfig', '-p'], text=True).find(lib) == -1:
                 missing_packages.append(package_name)


    if missing_packages:
        print("\n--- Missing System Dependencies ---")
        print("To run the main program, some system libraries need to be installed first.")
        print("Please run the following command to install them:")
        print(f"\n    sudo apt update && sudo apt install -y {' '.join(sorted(list(set(missing_packages))))}\n")
        sys.exit(1)
    else:
        print("System dependencies are satisfied.")
        return True

def create_and_install_python_packages():
    """
    Creates a virtual environment and installs Python dependencies.
    """
    venv_dir = "venv"
    if not os.path.exists(venv_dir):
        print("\nCreating Python virtual environment...")
        try:
            venv.create(venv_dir, with_pip=True)
            print("Virtual environment 'venv' created successfully.")
        except Exception as e:
            print(f"Error creating virtual environment: {e}")
            sys.exit(1)
    else:
        print("\nVirtual environment 'venv' already exists.")

    pip_executable = os.path.join(venv_dir, "bin", "pip")

    required_python_packages = [
        "PySide6",
        "pyusb",
        "matplotlib",
        "Pillow",
        "brother-ql-next",
        "schedule",
        "requests",
        "Flask",
        "zeroconf"
    ]

    print("\nInstalling required Python packages into the virtual environment...")
    try:
        subprocess.check_call([pip_executable, "install"] + required_python_packages)
        print("All Python dependencies installed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Error installing dependencies: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if sys.platform != "linux":
        print("This installer is configured for Ubuntu Linux.")
        # Exit gracefully if not on Linux
        sys.exit(0)

    if os.geteuid() == 0:
        print("Please do not run this script as root (with sudo).")
        sys.exit(1)
        
    if check_system_dependencies():
        create_and_install_python_packages()
        print("\n--- Setup Complete ---")
        print("You can now run your main program.")
        print("Use this command to run it:\n")
        print(f"    {os.path.join(os.getcwd(), 'venv/bin/python')} your_main_program.py\n")