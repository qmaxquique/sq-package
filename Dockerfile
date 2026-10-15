FROM docker.io/library/eclipse-temurin:21-jre-noble@sha256:0c324fbe2e1455c3159184717440f3b52906ef82a39c4a6efc9edb401d31971a

# This distribution uses the native Java runtime from the multi-architecture base.
# SHA256 of SonarSource's sonar-scanner-cli-8.1.0.6389.zip.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git python3 unzip \
    && curl --fail --location --retry 3 --output /tmp/scanner.zip \
        https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/sonar-scanner-cli-8.1.0.6389.zip \
    && printf '%s\n' 'ab76ab3c360025e9108be5b55be066f304a164f8b2850d2f2f333915db51bc1b  /tmp/scanner.zip' | sha256sum --check - \
    && unzip -q /tmp/scanner.zip -d /opt \
    && mv /opt/sonar-scanner-8.1.0.6389 /opt/sonar-scanner \
    && chmod -R a+rX /opt/sonar-scanner \
    && rm -f /tmp/scanner.zip \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/sonar-scanner/bin:${PATH}" \
    HOME=/tmp \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SONAR_USER_HOME=/state/cache

COPY scripts/sonar.py /opt/sonar-package/sonar.py
COPY scripts/report.py /opt/sonar-package/report.py
USER 1000:1000
WORKDIR /tmp
ENTRYPOINT ["python3", "/opt/sonar-package/sonar.py"]
