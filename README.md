<img src="Assets/Images/CAMS-Logo.png" alt="CAMS" width="300"/>

**Church Attendance Management System**

A user-friendly, free, and open-source tool to simplify attendance management and event check-ins for organizations like churches.

![Home Screen Screenshot](Assets/Images/screenshot01.png)

**About The Project**

CAMS was created to provide a simple, no-cost solution for churches and other small organizations to track attendance for their services and events. CAMS focuses on the core needs of tracking who attended and when, as well as printing name tags to help foster a welcoming environment.
Key Features

- Attendance Tracking: Easily record attendance for different events over time.

- Name Tag Printing: Quickly print name tags for attendees, guests, and children.

- Automated Setup: An installer script to check for dependencies and set up the program's environment.

- Cross-Platform (with caveats): Primarily built for Linux, but can run on Windows for users of Brother QL-series label printers.

- Customizable: Easily configure the program with your own organization's logo.

**Getting Started**

This guide will walk you through setting up and running CAMS on your local machine.
Platform Support

This project is developed primarily for Debian-based Linux distributions (like Ubuntu), but can be run on Windows with a notable limitation.
Operating System	Support Level	Printing Note
Ubuntu / Debian:	Full	Fully supported. Uses the system's CUPS printing service.
Windows:	Partial	The application runs, but printing is only supported for Brother QL-series label printers due to the brother-ql-next library.
Prerequisites

- Linux (Ubuntu/Debian):
  Python 3 (e.g., python3.8 or newer)
  The python3-venv and python3-pip packages. You can install them with:
            
        sudo apt update && sudo apt install python3-venv python3-pip

- Windows:
  Python 3 installed from python.org. Ensure you check the box "Add Python to PATH" during installation.

**Installation** (Linux)

The installation process is handled by a guided script.

1.) Clone the repository:
Open your terminal and clone this project to your machine.
  
        git clone https://github.com/joemoco7/CAMS.git

  

  - (Or, you can download the ZIP and extract it).

2.) Navigate to the directory:

        cd CAMS

3.) Run the Installer Script:
This script will check for system libraries, create a Python virtual environment (venv), and install all required packages. 

        python3 CAMS_Installer.py
  
  *   If any system dependencies are missing, the script will notify you and provide the `sudo apt install ...` command to run.
  *   **Important:** Do not run this script with `sudo`.

Once the script finishes, your environment is ready.

**Running After Installation**

After a successful installation, you can run the application using the provided shortcut files. This is the recommended method for daily use.
Recommended Launch Method: Desktop Shortcuts

**- For Linux Users (.desktop file) -**

The "Start CAMS.desktop" file is a standard Linux shortcut that will add CAMS to your system's application menu. You must edit this file before it will work.
  Open the Shortcut File: Navigate to the CAMS project folder and open "Start CAMS.desktop" in a text editor.

  Update the Path: Find the line that starts with Path=. You must replace the example path (/home/acer/Church_Attendance_Management_System/) with the full, absolute path to the CAMS folder on your computer.

        Before: Path=/home/acer/Church_Attendance_Management_System/

        After (Example): Path=/home/your_username/Documents/CAMS/

  Update the Icon (Optional but Recommended): Find the line that starts with Icon=. You can point this to the logo included in the project or any other icon file you prefer. Again, you must use the full, absolute path.

        Before: Icon=/home/acer/.local/share/applications/CAMS-icon.png

        After (Example): Icon=/home/your_username/Documents/CAMS/assets/images/logo.png

  Install the Shortcut: Save your changes to the file. Now, copy the edited file to your local applications directory using the terminal:

  - You can use this when stating from inside the CAMS project folder
    
        cp "Start CAMS.desktop" ~/.local/share/applications/

      

After these steps, "CAMS" should appear in your application launcher (you may need to log out and back in for it to show up).

    - Note for non-GNOME users: This shortcut uses gnome-terminal to launch the script. If you are using a different desktop environment (like KDE Plasma or XFCE), you may need to change gnome-terminal -- bash -c '...' to the equivalent for your system's terminal (e.g., konsole -e bash -c '...').

**- For Windows Users (.bat file) -**

The "Start CAMS.bat" file is designed to work without any modification.

  Simply navigate to the CAMS folder in File Explorer.

  Double-click the "Start CAMS.bat" file.

A command prompt window will appear, and the CAMS application will launch. The window will remain open after you close the application; you can press any key to dismiss it. This is intentional, as it allows you to see any error messages if the program closes unexpectedly.

**- Alternative Method: Running from the Terminal -**

This method is useful for debugging or if you prefer using the command line.

  Open a terminal (Linux) or Command Prompt/PowerShell (Windows).

  Navigate to the CAMS project directory.

    
      cd /path/to/your/CAMS/folder

  

Run the launcher script directly:

  On Linux:
  
    python3 CAMS_Launch.py

  On Windows:
  
    python CAMS_Launch.py

**Quick Start Guide**

Follow these steps to get CAMS up and running for the first time.

    Launch the Application: Start the CAMS program on one or more computers.

    Network Connection: Ensure all computers running CAMS are connected to the same local network (e.g., the same Wi-Fi). The first instance of the program that starts will automatically become the "Server," and other instances will connect to it. You will see a "Running as Server" or "Connected as Client" status on the main screen.

    - Connect a Printer:

        On the main screen, click the 🔴 Printer Connection button.

        A dialog will appear, scanning for available printers.

        Select a printer from the list that shows an "Available" status.

        Once connected, the button on the main screen will turn green (🟢 Printer Connection).

    - Add Your Branding (Optional):

        Click the Admin Dashboard button on the main screen.

        Go to the Branding tab.

        Update the Welcome Banner Text and provide a path to your church's logo.

        Click Save Branding Settings.

    - Start Checking People In!

        For returning attendees, use the Returnee Check-in option.

        For new guests, use the New Attendee option to register them.

**In-Depth Feature Guide**

**Main Screen Functions**

The main screen is your central hub for daily operations and accessing key settings.

**- Returnee Check-In -**

This is the primary screen for logging attendance for anyone who has attended before.

    How to use:

        Click the Returnee Check-in card from the home screen.

        Use the search bar or the large on-screen virtual keyboard to start typing a person's name. The "All Members" list will filter as you type.

        Alternatively, the top three matches will appear as large "Quick Select" buttons for faster access.

        Select the correct name from the list and click the Check In Selected button, or simply click their Quick Select button.

        A confirmation message will appear, their attendance will be logged, and a name tag will be printed automatically.

    Signed-In List: The list on the right shows everyone who is currently checked in for the day. Attendance is logged for a 6-hour period.

    Removing a Check-in: If you make a mistake, select the name from the "Currently Checked In" list on the right and click Remove Check-in.

**- New Attendee -**

This screen is for registering first-time visitors or new members.

    How to use:

        Click the New Attendee card from the home screen.

        Enter the person's First Name and Last Name.

        Assign them a Church Role from the dropdown list (e.g., "Visitor").

        You have two options:

            Register & Check In: This saves their profile, immediately logs their attendance for the day, and prints a name tag.

            Register Only: This saves their profile in the system for future check-ins without logging their attendance for today.

**- Edit Profile -**

This screen allows you to modify information for any existing profile.

    How to use:

        Click the Edit Profile button in the top navigation bar.

        Use the search bar to find the person you wish to edit.

        Select their name from the list on the left.

        Their profile details will appear in the form on the right.

        Make any necessary changes to their name or role and click Save Changes. You can also delete their profile entirely by clicking Delete Profile.

**- Printer Connection -**

This button on the bottom of the home screen serves two purposes:

    Status Indicator: Shows Green (🟢) if a printer is connected and Red (🔴) if it is not.

    Shortcut: Clicking this button opens the printer selection dialog, allowing you to quickly connect to or switch printers.

**- Connect / Re-Scan -**

Click this button to force your CAMS instance to re-scan the network for other running instances. This is useful if you start another computer after the server is already running or if a connection was lost.

**- Go Offline -**

Click this button to disconnect your CAMS instance from the network sync. The program will run in a standalone mode, and any check-ins or changes made will only be saved locally on that machine until you reconnect.
Admin Dashboard

**The Admin Dashboard is the control center for configuring all CAMS settings and generating reports.**

**- Printer Settings Tab -**

This is where you configure your label printer.

    Printing System: Choose between "Brother QL" (for direct connection) or "CUPS" (for Linux/macOS system printers).

    Scan for Printers: Click this to detect compatible printers on your network or connected via USB.

    Discovered Printers: Select a found printer from this dropdown list. Its details will auto-fill below.

    Identifier: This is the specific address of the printer (e.g., usb://0x04f9:0x2042 or a network IP).

    Label Size: Ensure this matches the label roll currently in your printer (e.g., 62x29).

    Print Test Page: Click this to print a sample label to confirm the connection is working.

    Save Printer Settings: Click to save your configuration.

**- Branding Tab -**

Customize the look and feel of the application.

    Welcome Banner Text: The text displayed in the blue bar at the very top of the application.

    Logo File Path: Click "Browse..." to select an image file (.png, .jpg, .svg) for your church's logo, which appears in the top navigation bar.

    Logo Fallback Text: Text that will be displayed if the logo image cannot be found.

**- Name Tag Editor Tab -**

Customize the layout of the printed name tags.

    Controls: On the left, you can adjust the position (X, Y), size (Width, Height), font, and style (Bold, Italic, Underline) for both the person's name and their role. You can also toggle whether to print the first name, last name, or role.

    Live Preview: The white box on the right shows a live preview of how your name tag will look.

    Save/Load: You can Save Layout or Load Defaults to revert to the original layout. It may require some testing to get the formatting printed exactly as desired.

**- Manual Reports Tab -**

Generate and export attendance reports.

    Start/End Date: Select the date range for your report.

    Email Recipients: If you plan to email the report, enter a comma-separated list of email addresses here.

    Generate Report: Creates an Excel (.xlsx) file with the attendance data and saves it to a "reports" folder on your computer.

    Generate & Email: Creates the same report and immediately emails it to the specified recipients (requires Email Settings to be configured).

**- Automated Reports Tab -**

Set up CAMS to automatically generate and email reports on a schedule.

    Configuration: Set the frequency (Daily, Weekly, etc.), day of the week, and time for the report to be sent.

    Recipients: Enter the email addresses of those who should receive the automated report.

    Save: Click Save Automation Settings to activate the schedule.

**- Email Settings Tab -**

Configure an email account that CAMS will use to send reports.

    Email Service: Select your provider (e.g., Gmail).

    Email Address: The "From" email address.

    Password / App Password: For services like Gmail, you must generate an "App Password" from your Google Account security settings. Your regular login password will not work.

    Test Connection: Click this after filling out the form to ensure CAMS can successfully log in to the email server.

**- Attendance Chart Tab -**

This tab displays a simple line graph of your church's attendance over the last four Sundays, giving you a quick visual overview of recent trends.
