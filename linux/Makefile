PYTHON ?= python3
PREFIX ?= /usr
DATA_DIR ?= /var/lib/ufi-tools

.PHONY: test smoke serve status install clean

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

# Start the backend against a throwaway data directory and the checked-in
# frontend, for a quick manual look.
smoke:
	UFI_TOOLS_DATA=$${TMPDIR:-/tmp}/ufi-tools-smoke \
		$(PYTHON) -m ufitools --data-dir $${TMPDIR:-/tmp}/ufi-tools-smoke serve

serve:
	$(PYTHON) -m ufitools serve

status:
	$(PYTHON) -m ufitools --data-dir $(DATA_DIR) status

install:
	./install.sh

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
