// Example Auth0 Action (Login flow / Post Login): limit Google sign-in to allowed domains.
// It pairs with the OIDC browser login configured in deploy/aws/terraform.
//
// Google sign-in is restricted to the domains in ALLOWED_DOMAINS plus an explicit allowlist.
// Email/password accounts are already invite-only (public sign-up is disabled on
// the database connection), so they pass through untouched.
//
// Install: Auth0 Dashboard -> Actions -> Library -> Create Action -> "Login / Post Login",
// paste this file, add two secrets, Deploy, then drag it into the Login flow.
//   ALLOWED_DOMAINS  comma-separated email domains, e.g. example.com
//   ALLOWED_EMAILS   comma-separated full addresses outside those domains that may sign in
exports.onExecutePostLogin = async (event, api) => {
  if (!event.connection || event.connection.strategy !== 'google-oauth2') return;

  const email = String(event.user.email || '').toLowerCase();
  const domain = email.split('@')[1] || '';
  const split = (value) =>
    String(value || '')
      .toLowerCase()
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
  const domains = split(event.secrets.ALLOWED_DOMAINS);
  const emails = split(event.secrets.ALLOWED_EMAILS);

  if (!event.user.email_verified) {
    api.access.deny('Your Google account email is not verified.');
    return;
  }
  if (domains.includes(domain) || emails.includes(email)) return;

  api.access.deny('Sign-in is limited to approved accounts. Ask your administrator for an invitation.');
};
