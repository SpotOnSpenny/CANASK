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

// Client-side flash, same markup the server's after_request hook injects OOB (base.jinja owns the
// #flashed-messages-container). Text goes in via textContent, never innerHTML.
function showFlash(text, category) {
  let container = document.getElementById("flashed-messages-container");
  if (!container) return;
  let wrap = document.createElement("div");
  wrap.className = "position-fixed top-0 start-50 translate-middle-x pt-3";
  wrap.style.cssText = "z-index: 1050; width: 50%;";
  let alert = document.createElement("div");
  alert.className = `alert alert-${category} alert-dismissible fade show`;
  alert.setAttribute("role", "alert");
  alert.textContent = text;
  let close = document.createElement("button");
  close.type = "button";
  close.className = "btn-close";
  close.setAttribute("data-bs-dismiss", "alert");
  alert.appendChild(close);
  wrap.appendChild(alert);
  container.replaceChildren(wrap);
}

// htmx 1.9 does not swap 4xx/5xx responses, so a failed hx-* request is a silent no-op unless the
// page handles it. Pages whose actions answer API-style status codes (the Feedback inbox: a card
// another admin already deleted -> 404, a failed commit -> 500) opt in with data-hx-flash-errors on
// an ancestor; everything else keeps its own existing error handling.
document.body.addEventListener("htmx:responseError", (event) => {
  let elt = event.detail.elt;
  if (!elt || !elt.closest("[data-hx-flash-errors]")) return;
  let status = event.detail.xhr ? event.detail.xhr.status : 0;
  let text;
  if (status === 404) {
    text = "That item no longer exists. Reload the page to refresh the list.";
  } else if (status === 403) {
    text = "You don't have permission to do that.";
  } else {
    text = `That action failed (${status || "network error"}). Please try again.`;
  }
  showFlash(text, "danger");
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

// reCAPTCHA-gated HTMX forms opt in via data-recaptcha-action="<action>" (login, forgot_password, ...):
// hold the request through htmx:confirm, fetch a token for that action, inject it as recaptcha-token,
// then resume via issueRequest (which re-serializes the form, picking up the injected field). When
// reCAPTCHA is disabled we don't intercept and the form posts as-is.
document.body.addEventListener("htmx:confirm", (event) => {
  let elt = event.detail.elt;
  let action = elt && elt.getAttribute && elt.getAttribute("data-recaptcha-action");
  if (!action) return;
  if (!recaptchaSiteKey()) return;            // disabled -> let HTMX proceed normally
  event.preventDefault();
  recaptchaToken(action).then((token) => {
    setHiddenField(elt, "recaptcha-token", token);
    event.detail.issueRequest(true);          // resume; true skips re-firing this confirm
  });
});

// --- Bootstrap-modal replacement for hx-confirm ------------------------------------------------
// Intercepts every hx-confirm'd request (event.detail.question is only set when hx-confirm is
// present, so the login/reCAPTCHA gate above and plain requests pass through untouched), holds the
// request, and shows the static #confirmActionModal from base.jinja. Confirm resumes the request
// via issueRequest(true); Cancel/X/backdrop/Esc just hide and drop it.
// Optional per-trigger attributes: data-confirm-title, data-confirm-button, data-confirm-class.
(function () {
  let pendingIssueRequest = null;
  let modalEl = document.getElementById("confirmActionModal");
  let okBtn = document.getElementById("confirm-action-ok");
  if (!modalEl || !okBtn) return;

  document.body.addEventListener("htmx:confirm", (event) => {
    if (!event.detail.question) return;
    event.preventDefault();

    let elt = event.detail.elt;
    // textContent throughout: confirm strings contain user-supplied values (invite emails)
    document.getElementById("confirm-action-title").textContent =
      elt.getAttribute("data-confirm-title") || "Please Confirm";
    document.getElementById("confirm-action-message").textContent = event.detail.question;
    okBtn.textContent = elt.getAttribute("data-confirm-button") || "Confirm";
    okBtn.className = "btn " + (elt.getAttribute("data-confirm-class") || "btn-primary");

    pendingIssueRequest = event.detail.issueRequest;
    bootstrap.Modal.getOrCreateInstance(modalEl).show();
  });

  // Wired once; the pending request is captured-and-cleared per open, so nothing stacks.
  okBtn.addEventListener("click", () => {
    let issue = pendingIssueRequest;
    pendingIssueRequest = null;
    okBtn.blur(); // avoid Chrome's aria-hidden-on-focused-element warning on hide
    bootstrap.Modal.getOrCreateInstance(modalEl).hide();
    if (issue) issue(true);
  });

  // Any dismissal path (Cancel, X, backdrop, Esc) drops the held request.
  modalEl.addEventListener("hidden.bs.modal", () => {
    pendingIssueRequest = null;
  });
})();

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

  // Character countdown: the textarea's maxlength silently stops input at the limit, so once the
  // message is within data-warn-within characters of data-max (both rendered by the server from
  // MAX_FEEDBACK_BODY) show an amber countdown, turning red when the limit is reached. Same re-init guard.
  let feedbackMessage = document.getElementById("feedback-message");
  let charCount = document.getElementById("feedback-char-count");
  if (feedbackForm && feedbackMessage && charCount && !feedbackMessage.dataset.countWired) {
    feedbackMessage.dataset.countWired = "true";
    let max = parseInt(feedbackMessage.dataset.max, 10) || parseInt(feedbackMessage.getAttribute("maxlength"), 10);
    let warnWithin = parseInt(feedbackMessage.dataset.warnWithin, 10) || 100;
    let updateCount = () => {
      let left = max - feedbackMessage.value.length;
      if (left > warnWithin) {
        charCount.hidden = true;
        charCount.textContent = "";
        return;
      }
      charCount.hidden = false;
      charCount.classList.toggle("at-limit", left <= 0);
      charCount.textContent = left <= 0
        ? `${max} character limit reached`
        : `Approaching ${max} character limit: ${left} character${left === 1 ? "" : "s"} left`;
    };
    feedbackMessage.addEventListener("input", updateCount);
    // form.reset() (after a successful submit) clears the value AFTER the reset event fires.
    feedbackForm.addEventListener("reset", () => setTimeout(updateCount, 0));
    updateCount();
  }
}

const GENERIC_FEEDBACK_ERROR = "There was an error submitting your feedback. Please try again later.";

// Render an error alert into the feedback widget. The message is built with textContent so a
// server-supplied string is never parsed as HTML.
function showFeedbackError(alertContainer, message) {
  let alert = document.createElement("div");
  alert.className = "alert alert-danger alert-dismissible fade show";
  alert.setAttribute("role", "alert");
  let p = document.createElement("p");
  p.style.marginBottom = "0";
  let strong = document.createElement("strong");
  strong.style.marginRight = "2px";
  strong.textContent = "Error! ";
  p.appendChild(strong);
  p.appendChild(document.createTextNode(message));
  let close = document.createElement("button");
  close.type = "button";
  close.className = "btn-close";
  close.setAttribute("data-bs-dismiss", "alert");
  close.setAttribute("aria-label", "Close");
  alert.appendChild(p);
  alert.appendChild(close);
  alertContainer.replaceChildren(alert);
}

function feedbackSubmit(token) {
  // validate the form has required fields
  let feedbackForm = document.getElementById("feedback-form")
  let feedbackData = new FormData(feedbackForm);
  let feedbackMessage = document.getElementById("feedback-message");
  let emailField = document.getElementById("feedback-email");
  // Email is OPTIONAL (the server accepts a blank one). When given, defer to the browser's
  // type="email" constraint -- the old hand-rolled regex rejected blank emails, "+" addresses, and
  // TLDs longer than three letters, so those submissions never reached the server at all. The
  // server re-validates with email_validator either way.
  let email = (feedbackData.get("email") || "").trim();
  if (email && !emailField.checkValidity()) {
    emailField.classList.add("is-invalid");
    emailField.value = "";
    emailField.placeholder = `"${email}"  is not a valid email address!`;
  } else if ((feedbackData.get("feedback") || "").trim() == "") {
    feedbackMessage.classList.add("is-invalid");
    feedbackMessage.value = "";
    feedbackMessage.placeholder = "This field cannot be blank";
  } else {
    try {
      emailField.classList.remove("is-invalid");
      feedbackMessage.classList.remove("is-invalid");
    } catch {}
    // submit the form data with the recaptcha token and the page the user is looking at
    // (hx-push-url keeps window.location current as the SPA navigates). The server validates it
    // and includes it in the stored row + the notification email.
    feedbackData.append("recaptcha-token", token);
    feedbackData.append("page", window.location.pathname + window.location.search);
    let alertContainer = document.getElementById("form-alerts");
    fetch("/feedback", {
      method: "POST",
      headers:{
        "X-CSRFToken": document.querySelector("meta[name='csrf-token']").getAttribute("content")
      },
      body: feedbackData,
    })
      .then((response) =>
        // Every reply is JSON. A 4xx carries an actionable message from the server (over-length
        // text, bad email, reCAPTCHA, rate limit) that the submitter can act on; "try again later"
        // is only right for a 5xx / network failure.
        response.json().catch(() => ({})).then((data) => {
          if (response.ok) return data;
          let message = (response.status < 500 && data && data.message) ? data.message : GENERIC_FEEDBACK_ERROR;
          return Promise.reject(new Error(message));
        }))
      .then((data) => {
        if (data["status"] == "success") {
          let feedbackAlert = `<div class="alert alert-success alert-dismissible fade show" role="alert">
          <p style="margin-bottom:0;"><strong style="margin-right: 2px;">Success! </strong> Your feedback has been submitted. Thank you for your input. </p>
          <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
          </div>`;
          alertContainer.innerHTML = feedbackAlert;
          feedbackForm.reset();
        } else {
          showFeedbackError(alertContainer, GENERIC_FEEDBACK_ERROR);
        }
      })
      .catch((error) => {
        showFeedbackError(alertContainer, (error && error.message) || GENERIC_FEEDBACK_ERROR);
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
