"""OAuth2 scopes, and a DRF permission that requires them.

An OAuth2 access token grants only the scopes its authorization was given,
which its application still has: a token's own claims are not trusted for
this, so taking a permission from an application, or disabling an
authorization, takes effect on the tokens already issued.

Other credentials (a session, HTTP Basic) act with all the user's rights, as
on openstreetmap.org, and scopes do not limit them.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission

# OpenStreetMap's scopes, each the name of a permission_<scope> field of
# Oauth2Application and Oauth2Authorization
SCOPES = ("read_prefs", "write_prefs", "write_diary", "write_api", "read_gpx", "write_gpx",
	"write_notes", "write_redactions", "consume_messages", "send_messages", "openid")

# What each scope lets an application do, as the consent page says it
SCOPE_DESCRIPTIONS = {
	"read_prefs": "Read your user preferences",
	"write_prefs": "Change your user preferences",
	"write_diary": "Write diary entries and comments",
	"write_api": "Change the map",
	"read_gpx": "Read your private GPS traces",
	"write_gpx": "Upload GPS traces",
	"write_notes": "Change notes",
	"write_redactions": "Redact map data",
	"consume_messages": "Read, change and delete your messages",
	"send_messages": "Send messages to other users",
	"openid": "Sign you in to it with your account here",
}

# What an application is given when it does not ask for particular scopes,
# as every authorization was before scopes could be asked for
DEFAULT_SCOPES = ("read_prefs", "write_api")

def parse_scope(text, app):
	"""The scopes a scope parameter (names separated by spaces) asks for, in
	the order of SCOPES, or DEFAULT_SCOPES for none. Raises ValueError, saying
	why, for a name that is not a scope or that the application may not ask for."""
	asked = set((text or "").split()) or set(DEFAULT_SCOPES)
	unknown = sorted(asked - set(SCOPES))
	if unknown:
		raise ValueError("Unknown scope: " + " ".join(unknown))
	refused = [scope for scope in SCOPES if scope in asked and not getattr(app, "permission_" + scope)]
	if refused:
		raise ValueError("The application may not ask for: " + " ".join(refused))
	return [scope for scope in SCOPES if scope in asked]

class TokenScopes(tuple):
	"""The scopes an OAuth2 access token grants, in the order of SCOPES. It is
	what TokenAuthentication gives as request.auth, and is a tuple of their
	names, with the authorization they came from."""

	def __new__(cls, scopes, authorization=None):
		granted = super().__new__(cls, scopes)
		granted.authorization = authorization
		return granted

def granted_scopes(authorization):
	"""The scopes an authorization grants: those it was given that its
	application still has."""
	app = authorization.parent_app
	return TokenScopes([scope for scope in SCOPES if getattr(authorization, "permission_" + scope)
		and getattr(app, "permission_" + scope)], authorization)

def scopes_required(read=None, write=None):
	"""A DRF permission class requiring an OAuth2 token to have a scope: read
	for the safe methods (GET, HEAD, OPTIONS), write for the rest; None for
	no scope. Other credentials pass, as does a request with none, which the
	view's other permissions decide on. Used with them, as in

		@permission_classes((IsAuthenticated, scopes_required(write="write_api")))

	A token without the scope is refused with 403 and a message naming it."""

	class ScopeRequired(BasePermission):
		def has_permission(self, request, view):
			if not isinstance(request.auth, TokenScopes):
				return True
			needed = read if request.method in SAFE_METHODS else write
			if needed is None or needed in request.auth:
				return True
			self.message = "This OAuth2 token has not been granted the {} scope".format(needed)
			return False

	ScopeRequired.__name__ = "ScopeRequired({}, {})".format(read, write)
	return ScopeRequired
