#!/bin/bash

cd /home/piuser/dev/_git/trmnl-todoist-today/proxy_layer || exit 1

# Activate the virtual environment
source venv/bin/activate

# Run your Python script
python todoist-update-trmnl.py