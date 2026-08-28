(function () {
    'use strict';

    var JEFFREY_ID = 'jeffrey-duck';
    var APPEARANCE_CHANCE = 0.1;
    var BUTTON_SIZE = 44;
    var EDGE_MARGIN = 32;
    var COLLISION_MARGIN = 8;
    var activeButton = null;
    var removalTimer = null;
    var safetyListenersAttached = false;
    var safetyObserver = null;
    var safetyObserverTimer = null;

    // Keep this allowlist explicit. New pages must opt in here deliberately.
    var ELIGIBLE_PATHS = ['/browse', '/series', '/discover', '/stats'];

    function isEligiblePage() {
        var path = window.location.pathname;
        return ELIGIBLE_PATHS.indexOf(path) !== -1 || /^\/item\/\d+$/.test(path);
    }

    function getViewport() {
        var visualViewport = window.visualViewport;
        var width = visualViewport && visualViewport.width || window.innerWidth;
        var height = visualViewport && visualViewport.height || window.innerHeight;
        var offsetLeft = visualViewport && visualViewport.offsetLeft || 0;
        var offsetTop = visualViewport && visualViewport.offsetTop || 0;

        return {
            width: width,
            height: height,
            pageLeft: window.scrollX + offsetLeft,
            pageTop: window.scrollY + offsetTop
        };
    }

    function setPosition(button, candidate, viewport) {
        var left = candidate.left !== undefined
            ? viewport.pageLeft + candidate.left
            : viewport.pageLeft + viewport.width - candidate.right - BUTTON_SIZE;
        var top = candidate.top !== undefined
            ? viewport.pageTop + candidate.top
            : viewport.pageTop + viewport.height - candidate.bottom - BUTTON_SIZE;
        var horizontalSafeArea = candidate.left !== undefined
            ? 'env(safe-area-inset-left, 0px)'
            : 'env(safe-area-inset-right, 0px)';
        var verticalSafeArea = candidate.top !== undefined
            ? 'env(safe-area-inset-top, 0px)'
            : 'env(safe-area-inset-bottom, 0px)';
        var horizontalSign = candidate.left !== undefined ? '+' : '-';
        var verticalSign = candidate.top !== undefined ? '+' : '-';

        button.style.left = 'calc(' + left + 'px ' + horizontalSign + ' ' + horizontalSafeArea + ')';
        button.style.top = 'calc(' + top + 'px ' + verticalSign + ' ' + verticalSafeArea + ')';
    }

    function inflate(rect, margin) {
        return {
            left: rect.left - margin,
            top: rect.top - margin,
            right: rect.right + margin,
            bottom: rect.bottom + margin
        };
    }

    function intersects(first, second) {
        return first.left < second.right && first.right > second.left &&
            first.top < second.bottom && first.bottom > second.top;
    }

    function isVisible(element) {
        var style = window.getComputedStyle(element);
        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
            return false;
        }

        var rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    }

    function collidesWithInteractiveUi(buttonRect) {
        var blockers = document.querySelectorAll([
            'a[href]',
            'button',
            'input',
            'select',
            'textarea',
            'form',
            'label',
            'summary',
            'details',
            'nav',
            '[tabindex]:not([tabindex="-1"])',
            '[contenteditable="true"]',
            '[role="button"]',
            '[role="link"]',
            '[role="dialog"]',
            '[role="alert"]',
            '[role="alertdialog"]',
            '[aria-modal="true"]',
            'dialog[open]',
            '.fixed.inset-0',
            '#toast-container',
            '#shortcut-modal',
            'video',
            'canvas',
            '[data-jeffrey-blocker]',
            '[id*="scanner"]',
            '[class*="scanner"]',
            '[id*="camera"]',
            '[class*="camera"]'
        ].join(','));
        var protectedRect = inflate(buttonRect, COLLISION_MARGIN);

        for (var index = 0; index < blockers.length; index += 1) {
            var blocker = blockers[index];
            if (blocker === activeButton || !isVisible(blocker)) continue;
            if (intersects(protectedRect, blocker.getBoundingClientRect())) return true;
        }

        return false;
    }

    function isInsideUsableViewport(rect, viewport) {
        return rect.left >= EDGE_MARGIN && rect.top >= EDGE_MARGIN &&
            rect.right <= viewport.width - EDGE_MARGIN &&
            rect.bottom <= viewport.height - EDGE_MARGIN;
    }

    function candidatePositions(viewport) {
        // These positions stay document-associated after placement and leave
        // a conservative margin for browser chrome and mobile safe areas.
        return [
            {top: 80, left: EDGE_MARGIN},
            {top: 80, right: EDGE_MARGIN},
            {bottom: EDGE_MARGIN, left: EDGE_MARGIN},
            {bottom: EDGE_MARGIN, right: EDGE_MARGIN},
            {top: Math.max(96, Math.floor(viewport.height / 2) - BUTTON_SIZE / 2), left: EDGE_MARGIN},
            {top: Math.max(96, Math.floor(viewport.height / 2) - BUTTON_SIZE / 2), right: EDGE_MARGIN}
        ];
    }

    function removeJeffrey() {
        if (removalTimer !== null) {
            window.clearTimeout(removalTimer);
            removalTimer = null;
        }

        if (activeButton) {
            activeButton.remove();
            activeButton = null;
        }

        if (safetyObserver) {
            safetyObserver.disconnect();
            safetyObserver = null;
        }
        if (safetyObserverTimer !== null) {
            window.clearTimeout(safetyObserverTimer);
            safetyObserverTimer = null;
        }

        if (safetyListenersAttached) {
            document.body.removeEventListener('htmx:afterSwap', removeJeffrey);
            window.removeEventListener('resize', removeJeffrey);
            window.removeEventListener('orientationchange', removeJeffrey);
            window.removeEventListener('beforeprint', removeJeffrey);
            safetyListenersAttached = false;
        }
    }

    function attachSafetyListeners() {
        if (safetyListenersAttached) return;
        document.body.addEventListener('htmx:afterSwap', removeJeffrey);
        window.addEventListener('resize', removeJeffrey);
        window.addEventListener('orientationchange', removeJeffrey);
        window.addEventListener('beforeprint', removeJeffrey);
        if (window.MutationObserver) {
            // Let deferred framework initialization finish before watching
            // layout mutations; this avoids treating startup churn as a
            // newly unsafe modal state.
            safetyObserverTimer = window.setTimeout(function () {
                safetyObserverTimer = null;
                if (!activeButton) return;
                safetyObserver = new MutationObserver(function () {
                    try {
                        if (!activeButton) return;
                        var viewport = getViewport();
                        var rect = activeButton.getBoundingClientRect();
                        if (!isInsideUsableViewport(rect, viewport) || collidesWithInteractiveUi(rect)) {
                            removeJeffrey();
                        }
                    } catch (error) {
                        removeJeffrey();
                    }
                });
                safetyObserver.observe(document.body, {
                    subtree: true,
                    childList: true,
                    attributes: true,
                    attributeFilter: ['aria-hidden', 'class', 'hidden', 'open', 'style']
                });
            }, 0);
        }
        safetyListenersAttached = true;
    }

    function closeAudioContext(context) {
        try {
            if (!context || typeof context.close !== 'function') return;
            var closing = context.close();
            if (closing && typeof closing.catch === 'function') closing.catch(function () {});
        } catch (error) {
            // Audio is optional; Jeffrey's visual behavior must continue.
        }
    }

    function playQuack() {
        var AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextConstructor) return;

        var context;
        try {
            context = new AudioContextConstructor();
            if (!context || context.state === 'closed') {
                closeAudioContext(context);
                return;
            }

            var start = context.currentTime;
            var oscillator = context.createOscillator();
            var gain = context.createGain();
            oscillator.type = 'triangle';
            oscillator.frequency.setValueAtTime(210, start);
            oscillator.frequency.exponentialRampToValueAtTime(135, start + 0.12);
            gain.gain.setValueAtTime(0.0001, start);
            gain.gain.exponentialRampToValueAtTime(0.04, start + 0.015);
            gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.17);
            oscillator.connect(gain);
            gain.connect(context.destination);
            oscillator.start(start);
            oscillator.stop(start + 0.18);

            if (context.state === 'suspended' && typeof context.resume === 'function') {
                var resumed = context.resume();
                if (resumed && typeof resumed.catch === 'function') resumed.catch(function () {});
            }
            window.setTimeout(function () { closeAudioContext(context); }, 250);
        } catch (error) {
            closeAudioContext(context);
        }
    }

    function activateJeffrey(event) {
        if (!activeButton || activeButton.disabled || activeButton.getAttribute('data-jeffrey-activated') === 'true') {
            return;
        }

        event.preventDefault();
        activeButton.disabled = true;
        activeButton.setAttribute('data-jeffrey-activated', 'true');
        playQuack();
        activeButton.classList.add('jeffrey--activated');

        var reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        removalTimer = window.setTimeout(removeJeffrey, reducedMotion ? 60 : 440);
    }

    function spawnJeffrey() {
        if (document.getElementById(JEFFREY_ID)) return;

        var button = document.createElement('button');
        button.id = JEFFREY_ID;
        button.type = 'button';
        button.className = 'jeffrey';
        button.setAttribute('aria-label', 'Jeffrey the duck');
        button.innerHTML = '<span aria-hidden="true">🦆</span>';
        button.style.visibility = 'hidden';
        document.body.appendChild(button);

        var viewport = getViewport();
        var candidates = candidatePositions(viewport);
        var placed = false;

        for (var index = 0; index < candidates.length; index += 1) {
            setPosition(button, candidates[index], viewport);
            var rect = button.getBoundingClientRect();
            if (isInsideUsableViewport(rect, viewport) && !collidesWithInteractiveUi(rect)) {
                placed = true;
                break;
            }
        }

        if (!placed) {
            button.remove();
            return;
        }

        button.style.visibility = 'visible';
        activeButton = button;
        button.addEventListener('click', activateJeffrey);
        attachSafetyListeners();
    }

    function initialize() {
        if (!isEligiblePage()) return;
        if (Math.random() >= APPEARANCE_CHANCE) return;
        spawnJeffrey();
    }

    function safelyInitialize() {
        try {
            initialize();
        } catch (error) {
            // Jeffrey is optional; never let an easter-egg failure affect Shelf.
            removeJeffrey();
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', safelyInitialize, {once: true});
    } else {
        safelyInitialize();
    }
})();
