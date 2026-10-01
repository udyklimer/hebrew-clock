import os
import tempfile

# Point the app at a throwaway data dir before anything imports app.core.config,
# so tests never touch the real clock.db.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="hebclk-test-")
