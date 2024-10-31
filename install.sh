#!/bin/bash
# Constants
EXTENSION_NAME="klipper_toolchanger"

# Force script to exit if an error occurs
set -e

# Find SRCDIR from the pathname of this script
SRCDIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

KLIPPER_PATH="${HOME}/klipper"
# Parse command line arguments to allow KLIPPER_PATH override
while getopts "k:h" arg; do
    case $arg in
        k) KLIPPER_PATH="$OPTARG";;
        h) echo "Usage: install.sh [-k KLIPPER_PATH]"; exit 0;;
        *) echo "Invalid option"; exit 1;;
    esac
done

# Verify conditions for the install to take place
check_preconditions() {
    if [ "$EUID" -eq 0 ]; then
        echo "This script must not run as root"
        exit 1
    fi

    if systemctl list-units --full --all -t service --no-legend | grep -qF "klipper.service"; then
        echo "Klipper service found!"
    else
        echo "Klipper service not found, please install Klipper first"
        exit 1
    fi
}

# Create symlinks to the extension files in the klippy/extras directory
link_extension() {
    if [ -d "${KLIPPER_PATH}/klippy/extras" ]; then
        echo "Linking ${EXTENSION_NAME} to Klippy extras..."
        ln -sf "${SRCDIR}"/*.py "${KLIPPER_PATH}/klippy/extras/"
    else
        echo "Error: ${KLIPPER_PATH}/klippy/extras does not exist. Verify KLIPPER_PATH."
