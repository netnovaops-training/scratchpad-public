@echo off
rem askpass.cmd - hands the NAS password from the NAS_SSH_PW environment variable to ssh (set only by nas.py).
"C:\Python314\python.exe" -c "import os,sys; sys.stdout.write(os.environ.get('NAS_SSH_PW','')+chr(10))"
