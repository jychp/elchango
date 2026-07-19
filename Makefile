.DEFAULT_GOAL := test

PYTHON ?= python3
VERSION ?=
DIST_DIR ?= dist

.PHONY: \
	setup \
	test test-versions test-app-macos test-web test-plugins test-plugin-cursor \
	test-plugin-claude test-plugin-streamdeck test-pocs \
	build build-app-macos build-app-macos-universal notarize-app-macos \
	verify-release-app-macos build-web build-plugins build-plugin-cursor \
	build-plugin-claude build-plugin-streamdeck \
	release clean

setup:
	npm --prefix web ci
	npm --prefix plugins/streamdeck ci
	swift package --package-path macos-app resolve

test: test-versions test-app-macos test-web test-plugins test-pocs
	git diff --check

test-versions:
	$(PYTHON) scripts/validate_versions.py

test-app-macos: test-versions
	swift test --package-path macos-app

test-web: test-versions
	npm --prefix web run check

test-plugins: test-plugin-cursor test-plugin-claude test-plugin-streamdeck

test-plugin-cursor: test-versions
	$(PYTHON) scripts/validate_provider_plugins.py cursor

test-plugin-claude: test-versions
	$(PYTHON) scripts/validate_provider_plugins.py claude
	@if command -v claude >/dev/null 2>&1; then \
		claude plugin validate ./plugins/claude --strict && \
		claude plugin validate . --strict; \
	else \
		echo "Claude CLI not found; custom strict validation completed."; \
	fi

test-plugin-streamdeck: test-versions
	cmp LICENSE plugins/streamdeck/com.jychp.elchango.sdPlugin/LICENSE
	test -s plugins/streamdeck/com.jychp.elchango.sdPlugin/THIRD_PARTY_NOTICES.md
	test -s plugins/streamdeck/com.jychp.elchango.sdPlugin/TRADEMARKS.md
	npm --prefix plugins/streamdeck run check
	npm --prefix plugins/streamdeck run validate

test-pocs:
	@set -eu; \
	for poc in scripts/poc/cursor/*.py scripts/poc/claude/*.py; do \
		$(PYTHON) -m py_compile "$$poc"; \
		$(PYTHON) "$$poc" --help >/dev/null; \
	done

build: build-app-macos build-plugins

build-app-macos: test-versions
	./macos-app/Scripts/package-app.sh

build-app-macos-universal: test-versions
	ELCHANGO_ARCHITECTURES="arm64 x86_64" \
		./macos-app/Scripts/package-app.sh

notarize-app-macos: build-app-macos-universal
	./macos-app/Scripts/notarize-app.sh

verify-release-app-macos:
	ELCHANGO_VERIFY_DISTRIBUTION=1 \
	ELCHANGO_VERIFY_NOTARIZATION=1 \
	ELCHANGO_EXPECTED_ARCHITECTURES="arm64 x86_64" \
		./macos-app/Scripts/verify-package.sh \
			macos-app/dist/elChango.app

build-web: test-versions
	npm --prefix web run build

build-plugins: \
	build-plugin-cursor build-plugin-claude build-plugin-streamdeck

build-plugin-cursor: test-plugin-cursor
	$(PYTHON) scripts/package_provider_plugin.py \
		cursor --output-dir "$(DIST_DIR)"

build-plugin-claude: test-plugin-claude
	$(PYTHON) scripts/package_provider_plugin.py \
		claude --output-dir "$(DIST_DIR)"

build-plugin-streamdeck: test-plugin-streamdeck
	npm --prefix plugins/streamdeck run pack

release:
	@if ! printf '%s\n' "$(VERSION)" | \
		grep -Eq '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$$'; then \
		echo "ERROR: VERSION must be strict SemVer without a v prefix." >&2; \
		exit 2; \
	fi
	@test "$$(git branch --show-current)" = "main" || { \
		echo "ERROR: releases must be created from main." >&2; \
		exit 2; \
	}
	@$(PYTHON) scripts/validate_versions.py "$(VERSION)"
	@git diff --quiet && git diff --cached --quiet || { \
		echo "ERROR: tracked files contain uncommitted changes." >&2; \
		exit 2; \
	}
	git fetch origin main
	@test "$$(git rev-parse HEAD)" = "$$(git rev-parse origin/main)" || { \
		echo "ERROR: local main must exactly match origin/main." >&2; \
		exit 2; \
	}
	@if git rev-parse --verify --quiet "refs/tags/v$(VERSION)" >/dev/null; then \
		echo "ERROR: tag v$(VERSION) already exists." >&2; \
		exit 2; \
	fi
	git tag -a "v$(VERSION)" -m "v$(VERSION)"
	git push origin "v$(VERSION)"

clean:
	rm -rf "$(DIST_DIR)" macos-app/dist web/dist
	rm -f plugins/streamdeck/*.streamDeckPlugin
