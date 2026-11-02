PYTHON ?= python3

.PHONY: test initdb serve doctor compile clean

test:
	$(PYTHON) -m unittest discover -s tests -v

initdb:
	$(PYTHON) -m rohtunnel initdb

serve:
	$(PYTHON) -m rohtunnel serve

doctor:
	$(PYTHON) -m rohtunnel doctor

compile:
	$(PYTHON) -m compileall -q rohtunnel tests

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .ruff_cache
