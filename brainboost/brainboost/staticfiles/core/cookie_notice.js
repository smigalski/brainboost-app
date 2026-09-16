(function () {
    var STORAGE_KEY = "bb_cookie_notice_v1_dismissed";
    var banner = document.getElementById("cookie-notice");
    var button = document.getElementById("cookie-notice-ack");

    if (!banner || !button) {
        return;
    }

    function isDismissed() {
        try {
            return window.localStorage.getItem(STORAGE_KEY) === "1";
        } catch (e) {
            return false;
        }
    }

    function rememberDismissed() {
        try {
            window.localStorage.setItem(STORAGE_KEY, "1");
        } catch (e) {
            /* ignore */
        }
    }

    function showBanner() {
        banner.classList.add("cookie-notice--visible");
        banner.setAttribute("aria-hidden", "false");
    }

    function hideBanner() {
        banner.classList.remove("cookie-notice--visible");
        banner.setAttribute("aria-hidden", "true");
    }

    if (!isDismissed()) {
        showBanner();
    } else {
        hideBanner();
    }

    button.addEventListener("click", function () {
        rememberDismissed();
        hideBanner();
    });
})();
