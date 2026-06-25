.PHONY: test compile shell-test full-shell-test python-test lint format-check security-check quality ci package-deb package-rpm package-rpm-container test-rpm-package zipapp clean-generated

PYTHON ?= $(shell if [ -x .venv/bin/python ]; then printf '%s' .venv/bin/python; else printf '%s' python3; fi)
BASELINE_SHELL_TESTS := \
	tests/test_access_policy.sh \
	tests/test_config_validation.sh \
	tests/test_deploy.sh \
	tests/test_diag.sh \
	tests/test_doctor.sh \
	tests/test_history_logs.sh \
	tests/test_hooks_profiles.sh \
	tests/test_init.sh \
	tests/test_init_guided.sh \
	tests/test_init_modern.sh \
	tests/test_plan.sh \
	tests/test_plan_summary.sh \
	tests/test_pull.sh \
	tests/test_pull_logs.sh \
	tests/test_rollback.sh \
	tests/test_security_checks.sh \
	tests/test_status.sh \
	tests/test_timeout.sh
ALL_SHELL_TESTS := $(sort $(wildcard tests/test_*.sh))

test: compile shell-test

compile:
	PYTHONPATH=src $(PYTHON) -m compileall -q src

shell-test:
	for test_script in $(BASELINE_SHELL_TESTS); do \
		echo "==> $$test_script"; \
		bash "$$test_script"; \
	done

full-shell-test:
	for test_script in $(ALL_SHELL_TESTS); do \
		echo "==> $$test_script"; \
		bash "$$test_script"; \
	done

python-test:
	PYTHONPATH=src $(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check src tests tools

format-check:
	$(PYTHON) tools/format-check.py

security-check:
	$(PYTHON) tools/security-check.py

quality: compile security-check lint format-check python-test

ci: quality shell-test

package-deb:
	./packaging/deb/build-deb.sh

package-rpm:
	./packaging/rpm/build-rpm.sh

package-rpm-container:
	./packaging/rpm/build-rpm-container.sh

# Air-gapped / pip-free build: single self-contained zipapp + offline .zip bundle.
# Needs only python3.12 + stdlib (no pip, no network, no compiler).
zipapp:
	./packaging/zipapp/build-zipapp.sh

test-rpm-package:
	./packaging/rpm/test-rpm.sh

clean-generated:
	find src tests -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache \) -prune -exec rm -rf {} +
	find src tests -type f -name '*.pyc' -delete
