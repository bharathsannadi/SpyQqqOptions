PY := venv/bin/python
UID := $(shell id -u)
.PHONY: test run dry status report web install uninstall

test:
	$(PY) -m pytest tests/ -q

run:
	$(PY) -m spyqqq run

dry:
	SPYQQQ_DRY_RUN=1 $(PY) -m spyqqq run --dry

status:
	$(PY) -m spyqqq status

report:
	$(PY) -m spyqqq report $(DAY)

web:
	$(PY) -m spyqqq web

install:
	cp launchd/com.spyqqq.plist launchd/com.spyqqq.web.plist launchd/com.spyqqq.watchdog.plist ~/Library/LaunchAgents/
	launchctl bootstrap gui/$(UID) ~/Library/LaunchAgents/com.spyqqq.plist
	launchctl bootstrap gui/$(UID) ~/Library/LaunchAgents/com.spyqqq.web.plist
	launchctl bootstrap gui/$(UID) ~/Library/LaunchAgents/com.spyqqq.watchdog.plist

uninstall:
	-launchctl bootout gui/$(UID)/com.spyqqq
	-launchctl bootout gui/$(UID)/com.spyqqq.web
	-launchctl bootout gui/$(UID)/com.spyqqq.watchdog
