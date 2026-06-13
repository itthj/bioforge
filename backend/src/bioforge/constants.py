DEFAULT_PROJECT_ID = "default-project"

# The identity every request resolves to when auth is OFF (BIOFORGE_AUTH_ENABLED=false, the
# default), and the owner that legacy/single-user data migrates to when auth is turned ON. It is
# NOT loginable -- it is created with a sentinel password hash that no password can verify against.
DEFAULT_USER_ID = "default-user"
DEFAULT_USER_EMAIL = "default@bioforge.local"
