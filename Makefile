PYTHON ?= python3

.PHONY: test initdb serve doctor compile clean

test:
	$(PYTHON) -m unittest discover -s tests -v

initdb:
	$(PYTHON) -m fn_webstore initdb

serve:
	$(PYTHON) -m fn_webstore serve

doctor:
	$(PYTHON) -m fn_webstore doctor

compile:
	$(PYTHON) -m compileall -q fn_webstore tests

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .ruff_cache
