const params = new URLSearchParams(window.location.search);
const error = document.getElementById("loginError");
if (error && params.has("error")) {
  error.textContent = "Wrong password. Try again.";
  error.hidden = false;
}
if (error && params.has("google_error")) {
  error.textContent = "Google sign-in was not accepted.";
  error.hidden = false;
}

fetch("/auth/config", { cache: "no-store" })
  .then((response) => (response.ok ? response.json() : null))
  .then((config) => {
    if (!config) return;
    const google = document.querySelector(".login-google");
    const separator = document.querySelector(".login-separator");
    const field = document.querySelector(".login-field");
    const submit = document.querySelector('.login-card button[type="submit"]');
    const password = document.getElementById("password");

    if (google && config.google_enabled) {
      google.hidden = false;
    }
    if (separator && config.google_enabled && config.password_enabled) {
      separator.hidden = false;
    }
    if (!config.password_enabled) {
      if (field) field.hidden = true;
      if (submit) submit.hidden = true;
      if (password) password.required = false;
    }
  })
  .catch(() => {});
