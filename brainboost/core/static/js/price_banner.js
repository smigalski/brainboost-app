(function () {
    var STORAGE_KEY = "bb_price_banner_v1_dismissed_at";
    var RESHOW_AFTER_DAYS = 14; // 0 = nie wieder anzeigen nach Dismiss
    var banner = document.getElementById("bb-price-banner");
    var closeBtn = document.getElementById("bb-price-banner-close");

    if (!banner || !closeBtn) {
        return;
    }

    function nowMs() {
        return Date.now();
    }

    function msFromDays(days) {
        return days * 24 * 60 * 60 * 1000;
    }

    function readDismissedAt() {
        try {
            var stored = window.localStorage.getItem(STORAGE_KEY);
            return stored ? parseInt(stored, 10) : null;
        } catch (e) {
            return null;
        }
    }

    function rememberDismissed() {
        try {
            window.localStorage.setItem(STORAGE_KEY, String(nowMs()));
        } catch (e) {
            /* ignore */
        }
    }

    function shouldShow() {
        var path = window.location.pathname || "";
        if (path.toLowerCase().includes("preise")) {
            return false;
        }
        var dismissedAt = readDismissedAt();
        if (!dismissedAt) {
            return true;
        }
        if (RESHOW_AFTER_DAYS === 0) {
            return false;
        }
        return nowMs() - dismissedAt >= msFromDays(RESHOW_AFTER_DAYS);
    }

    function updateBodyPadding() {
        if (banner.hasAttribute("hidden")) {
            document.body.style.paddingBottom = "";
            return;
        }
        var height = banner.offsetHeight || 0;
        document.body.style.paddingBottom = height ? height + "px" : "";
    }

    function showBanner() {
        banner.removeAttribute("hidden");
        banner.setAttribute("aria-hidden", "false");
        updateBodyPadding();
    }

    function hideBanner() {
        banner.setAttribute("hidden", "");
        banner.setAttribute("aria-hidden", "true");
        updateBodyPadding();
    }

    if (shouldShow()) {
        showBanner();
    } else {
        hideBanner();
    }

    closeBtn.addEventListener("click", function () {
        rememberDismissed();
        hideBanner();
    });

    window.addEventListener("resize", updateBodyPadding);
})();
