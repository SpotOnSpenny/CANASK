// CSRF token handling for htmx requests
document.body.addEventListener('htmx:configRequest', (event) => {
    event.detail.headers['X-CSRFToken'] = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
});

// The server rotates the CSRF token at privilege change (login clears the session), but the login
// response is an HTMX partial swap that never re-renders base.jinja's meta tag. It hands the fresh
// token over via an HX-Trigger event instead; keep the meta tag current so configRequest above
// sends a token that matches the new session.
document.body.addEventListener('csrfTokenRefresh', (event) => {
    document.querySelector('meta[name="csrf-token"]').setAttribute('content', event.detail.token);
});

// --- reCAPTCHA v3 -----------------------------------------------------------------------------
// The site key is rendered into a <meta> only when reCAPTCHA is enabled (see base.jinja); when it's
// absent (dev, RECAPTCHA_ENABLED=false) every helper below no-ops and the server verifier returns
// ok, so forms submit normally.
function recaptchaSiteKey() {
  let meta = document.querySelector('meta[name="recaptcha-site-key"]');
  return meta ? meta.getAttribute("content") : "";
}

// Resolve to a fresh v3 token for `action`, or "" when reCAPTCHA is disabled / unavailable (the
// server fails closed on an empty token when enabled, so "" is safe to submit). Never rejects, so
// callers don't have to guard the promise. Fetched per-submit because v3 tokens expire in ~2 min.
function recaptchaToken(action) {
  return new Promise((resolve) => {
    let siteKey = recaptchaSiteKey();
    if (!siteKey || typeof grecaptcha === "undefined") {
      resolve("");
      return;
    }
    grecaptcha.ready(() => {
      grecaptcha.execute(siteKey, { action: action }).then(resolve, () => resolve(""));
    });
  });
}

function setHiddenField(form, name, value) {
  let input = form.querySelector('input[name="' + name + '"]');
  if (!input) {
    input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    form.appendChild(input);
  }
  input.value = value;
}

// Login is an HTMX form, so gate its request through htmx:confirm: hold the request, fetch a token,
// inject it as recaptcha-token, then resume via issueRequest (which re-serializes the form, picking
// up the injected field). When reCAPTCHA is disabled we don't intercept and the form posts as-is.
document.body.addEventListener("htmx:confirm", (event) => {
  let elt = event.detail.elt;
  if (!elt || elt.id !== "login-form") return;
  if (!recaptchaSiteKey()) return;            // disabled -> let HTMX proceed normally
  event.preventDefault();
  recaptchaToken("login").then((token) => {
    setHiddenField(elt, "recaptcha-token", token);
    event.detail.issueRequest(true);          // resume; true skips re-firing this confirm
  });
});

//HTMX config to exclude history cache and require server request on back/forward
htmx.config.historyCacheSize = 0;
htmx.config.refreshOnHistoryMiss = true;
// Parse swap responses with <template> tags. Without this, a response whose main
// content is a bare <tr> (e.g. the group/user/invite row partials) is parsed in a
// table context that foster-parents the appended out-of-band flash <div>, throwing
// "querySelectorAll is not a function" and wiping out the swapped row.
htmx.config.useTemplateFragments = true;


// Initiate the mobile nav when it's present on the page
function toggleHamburger(e, navToggle, bars) {
    bars.forEach((bar) => bar.classList.toggle("x"));
    navToggle.classList.toggle("menu-active");
    navLinks.forEach((link) => link.classList.toggle("visible"));
    navLinkContainer.classList.toggle("expanded");
    mobileTitle.classList.toggle("visible");
}

function initMobileNav(){
  let navToggle = document.querySelector(".nav-toggle");
  let navLinks = document.querySelectorAll(".nav-title, .mobile-nav-link");
  let mobileTitle = document.querySelector(".mobile-title");
  let navLinkContainer = document.querySelector("#mobile-nav-link-container");
  let bars = document.querySelectorAll(".bar");

  // Helper: toggle the hamburger state
  function toggleHamburger() {
    navToggle.classList.toggle("active");
    bars.forEach((bar) => bar.classList.toggle("x"));
  }

  // Click handler for the mobile title
  mobileTitle.addEventListener("click", () => {
    if (navLinkContainer.classList.contains("expanded")) {
      toggleHamburger();
    }
  });

  // Example: toggle menu open/close
  navToggle.addEventListener("click", () => {
    navLinkContainer.classList.toggle("expanded");
    toggleHamburger();
  });

  // Optional: close menu when a link is clicked
  navLinks.forEach((link) => {
    link.addEventListener("click", () => {
      if (navLinkContainer.classList.contains("expanded")) {
        navLinkContainer.classList.remove("expanded");
        toggleHamburger();
      }
    });
  });
}

// Initiate the feedback form when it's present on the page
function initFeedback() {
  let feedbackToggle = document.querySelector(".feedback-toggle");
  let feedbackContent = document.querySelector(".feedback-content-container");
  let feedbackClose = document.querySelector(".feedback-close");

  function toggleFeedback() {
    feedbackContent.classList.toggle("feedback-visible");
    feedbackToggle.classList.toggle("feedback-toggle-invisible");
  }

  feedbackToggle.addEventListener("click", toggleFeedback);
  feedbackClose.addEventListener("click", toggleFeedback);

  // reCAPTCHA v3 is invisible (no widget callback like v2), so intercept the submit, fetch a token,
  // then run the existing validate+fetch path. Guarded so re-init doesn't stack listeners.
  let feedbackForm = document.getElementById("feedback-form");
  if (feedbackForm && !feedbackForm.dataset.recaptchaWired) {
    feedbackForm.dataset.recaptchaWired = "true";
    feedbackForm.addEventListener("submit", (e) => {
      e.preventDefault();
      recaptchaToken("feedback").then((token) => feedbackSubmit(token));
    });
  }
}

function validateEmail(mail) {
  if (/^\w+([\.-]?\w+)*@\w+([\.-]?\w+)*(\.\w{2,3})+$/.test(mail)) {
    return true;
  }
  return false;
}

function feedbackSubmit(token) {
  // validate the form has required fields
  let feedbackForm = document.getElementById("feedback-form")
  let feedbackData = new FormData(feedbackForm);
  let feedbackMessage = document.getElementById("feedback-message");
  let emailField = document.getElementById("feedback-email");
  if (validateEmail(feedbackData.get("email")) == false) {
    emailField.classList.toggle("is-invalid");
    emailField.value = "";
    emailField.placeholder = `"${feedbackData.get(
      "email"
    )}"  is not a valid email address!`;
  } else if (feedbackData.get("feedback") == "") {
    feedbackMessage.classList.toggle("is-invalid");
    feedbackMessage.value = "";
    feedbackMessage.placeholder = "This field cannot be blank";
  } else {
    try {
      emailField.classList.remove("is-invalid");
      feedbackMessage.classList.remove("is-invalid");
    } catch {}
    // submit the form data with the recaptcha token
    feedbackData.append("recaptcha-token", token);
    let alertContainer = document.getElementById("form-alerts");
    fetch("/feedback", {
      method: "POST",
      headers:{
        "X-CSRFToken": document.querySelector("meta[name='csrf-token']").getAttribute("content")
      },
      body: feedbackData,
    })
      .then((response) => {
        if (response.ok) {
          return response.json();
        } else {
          console.log("response not ok");
          console.log(response);
          return Promise.reject(response);
        }
      })
      .then((data) => {
        if (data["status"] == "success") {
          let feedbackAlert = `<div class="alert alert-success alert-dismissible fade show" role="alert">
          <p style="margin-bottom:0;"><strong style="margin-right: 2px;">Success! </strong> Your feedback has been submitted. Thank you for your input. </p>
          <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
          </div>`;
          alertContainer.innerHTML = feedbackAlert;
          feedbackForm.reset();
        } else {
          let feedbackAlert = `
        <div class="alert alert-danger alert-dismissible fade show" role="alert">
          <p style="margin-bottom:0;"><strong style="margin-right: 2px;">Error! </strong>There was an error submitting your feedback. Please try again later.</p>
          <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
        </div>
        `;
          alertContainer.innerHTML = feedbackAlert;
        }
      })
      .catch((error) => {
        let feedbackAlert = `
      <div class="alert alert-danger alert-dismissible fade show" role="alert">
        <p style="margin-bottom:0;"><strong style="margin-right: 2px;">Error! </strong><p>There was an error submitting your feedback. Please try again later.</p>
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
      </div>
      `;
        alertContainer.innerHTML = feedbackAlert;
      });
  }
}

// Watch for HTMX signals to know when to run the mobile nav and feedback init functions
document.body.addEventListener("htmx:afterSettle", (event) => {
  if (event.detail.target && event.detail.target.id == "page-container") {
    let scriptContainer = document.querySelector("#template-scripts-signal");
    if (scriptContainer.classList.contains("initialized")) {
      return; 
    } else {
      initMobileNav();
      initFeedback();
      scriptContainer.classList.add("initialized");
    }
  }
});

document.addEventListener("DOMContentLoaded", (event) => {
  let scriptContainer = document.querySelector("#template-scripts-signal");
  if (scriptContainer && !scriptContainer.classList.contains("initialized")) {
    initMobileNav();
    initFeedback();
    scriptContainer.classList.add("initialized");
  }
});

// Highlight the side-nav (and mobile-nav) link for the page currently shown.
// The nav persists across HTMX swaps, so this runs on load and after every
// navigation, matching window.location against each link's hx-push-url.
function highlightActiveNav() {
  function normalize(p) { return (p || "").replace(/\/+$/, "") || "/"; }
  let current = normalize(window.location.pathname);
  let links = document.querySelectorAll(
    "#desktop-nav .nav-link, #mobile-nav .mobile-nav-link"
  );
  links.forEach((link) => {
    let target = normalize(link.getAttribute("hx-push-url") || link.getAttribute("hx-get"));
    let isCurrent = target === current;
    link.classList.toggle("nav-current", isCurrent);
    // Announce the current page to assistive tech (replaces the static
    // aria-current that used to sit on every link).
    if (isCurrent) {
      link.setAttribute("aria-current", "page");
    } else {
      link.removeAttribute("aria-current");
    }
  });
}

document.addEventListener("DOMContentLoaded", highlightActiveNav);
// Fires when HTMX pushes a new URL (province click) and on back/forward restore
document.body.addEventListener("htmx:pushedIntoHistory", highlightActiveNav);
document.body.addEventListener("htmx:historyRestore", highlightActiveNav);
