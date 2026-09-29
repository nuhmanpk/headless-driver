# Chrome, a matching chromedriver, and headless-driver.
#
# Both are pinned and baked in, so webdriver-manager never downloads anything at
# runtime — a container that reaches out on first search fails in exactly the
# environments where you cannot debug it.
#
#   docker build -t headless-driver .
#   docker run --rm --shm-size=1g headless-driver search "python headless"
#   docker run --rm headless-driver search "site:linkedin.com/in jane doe" --mode aggregate
#
# --shm-size matters: Chrome's default /dev/shm in Docker is 64 MB and it will
# crash on real pages without it. --disable-dev-shm-usage is already set, which
# covers most cases, but a larger shm is still the more reliable fix.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/app

# tini reaps the zombie processes Chrome leaves behind; without it a
# long-running container accumulates defunct renderers until it runs out of PIDs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg unzip tini \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] \
        https://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Fetch the chromedriver matching the Chrome that apt just installed. Chrome for
# Testing does not publish a build for every apt version, so fall back to the
# newest driver in the same major version.
RUN set -eux; \
    CHROME_VERSION="$(google-chrome --version | grep -oE '[0-9]+(\.[0-9]+){3}')"; \
    MAJOR="${CHROME_VERSION%%.*}"; \
    BASE="https://storage.googleapis.com/chrome-for-testing-public"; \
    URL="${BASE}/${CHROME_VERSION}/linux64/chromedriver-linux64.zip"; \
    if ! curl -fsI "$URL" >/dev/null 2>&1; then \
        CHROME_VERSION="$(curl -fsSL \
            "https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_${MAJOR}")"; \
        URL="${BASE}/${CHROME_VERSION}/linux64/chromedriver-linux64.zip"; \
    fi; \
    curl -fsSL -o /tmp/chromedriver.zip "$URL"; \
    unzip -q /tmp/chromedriver.zip -d /tmp; \
    mv /tmp/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver; \
    chmod +x /usr/local/bin/chromedriver; \
    rm -rf /tmp/chromedriver.zip /tmp/chromedriver-linux64; \
    chromedriver --version

# Chrome will not run as root without --no-sandbox, and needs a writable HOME
# for its profile directories.
RUN useradd --create-home --home-dir /home/app app
WORKDIR /app

# [impersonate] gives a real browser TLS fingerprint, which is what gets
# answers from cloud addresses; [http] is the plain-requests fallback.
RUN pip install --no-cache-dir "headless-driver[impersonate,http,fast]"

USER app
ENV CHROME_DRIVER_PATH=/usr/local/bin/chromedriver

ENTRYPOINT ["/usr/bin/tini", "--", "headless-driver"]
CMD ["doctor"]
