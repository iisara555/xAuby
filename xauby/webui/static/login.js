if (new URLSearchParams(window.location.search).has("error")) {
  const error = document.getElementById("loginError");
  if (error) error.hidden = false;
}
