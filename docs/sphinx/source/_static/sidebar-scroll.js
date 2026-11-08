// The Read the Docs theme calls scrollIntoView() on the current menu item
// on load and on every hash change, which pins that item to the top of the
// left menu. Keep the menu's own scroll position instead.
(function () {
  var storageKey = "memchentic-docs-sidebar-scroll";
  var acceptScroll = false;
  var originalScrollIntoView = Element.prototype.scrollIntoView;

  function menuScroller() {
    return document.querySelector(".wy-side-scroll");
  }

  function savedScroll() {
    try {
      var raw = sessionStorage.getItem(storageKey);
    } catch (error) {
      return null;
    }
    if (raw == null) return null;
    var y = Number(raw);
    return Number.isFinite(y) ? y : null;
  }

  function saveScroll() {
    if (!acceptScroll) return;
    var scroller = menuScroller();
    if (!scroller) return;
    try {
      sessionStorage.setItem(storageKey, String(scroller.scrollTop));
    } catch (error) {
      // Private browsing can reject sessionStorage. The menu still works.
    }
  }

  function restoreScroll() {
    var y = savedScroll();
    var scroller = menuScroller();
    if (y == null || !scroller) return false;
    if (scroller.scrollTop !== y) scroller.scrollTop = y;
    return true;
  }

  function revealInMenu(item) {
    var scroller = menuScroller();
    if (!scroller) return;
    var itemRect = item.getBoundingClientRect();
    var menuRect = scroller.getBoundingClientRect();
    if (itemRect.top < menuRect.top) {
      scroller.scrollTop -= menuRect.top - itemRect.top;
    } else if (itemRect.bottom > menuRect.bottom) {
      scroller.scrollTop += itemRect.bottom - menuRect.bottom;
    }
  }

  Element.prototype.scrollIntoView = function (arg) {
    var inMenu = this.closest && this.closest(".wy-menu-vertical");
    if (!inMenu) return originalScrollIntoView.apply(this, arguments);
    if (restoreScroll()) return;
    revealInMenu(this);
  };

  function watchMenu() {
    var scroller = menuScroller();
    if (!scroller) return;
    scroller.addEventListener("scroll", saveScroll, { passive: true });
    window.addEventListener("pagehide", saveScroll);
  }

  function arm() {
    watchMenu();
    window.addEventListener("load", function () {
      if (savedScroll() == null) {
        acceptScroll = true;
        saveScroll();
      } else {
        restoreScroll();
      }
      acceptScroll = true;
    });
    window.addEventListener("pageshow", restoreScroll);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", arm);
  } else {
    arm();
  }
})();
