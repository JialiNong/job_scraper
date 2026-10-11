# Mobile UI Fixes Summary

Merged result of `main` (PR #2) plus this branch's extra mobile polish
(tighter funnel/journey spacing, Refresh `nowrap`, smaller tablet bubbles).

## Issues Fixed

### 1. Header Buttons Layout
**Problem:** On mobile, header buttons sat next to the title and were hard to tap.
The Refresh icon + label could also wrap onto two lines.

**Solution:**
- Title on its own row; buttons on a second horizontally scrollable row
- Hidden scrollbar, swipe left/right
- `white-space: nowrap` on `.nav-btn` and header Refresh so icon+text stay on one line
- `flex-shrink: 0` so buttons keep their size instead of being squeezed

```css
@media (max-width: 768px) {
  header {
    flex-direction: column;
    align-items: stretch;
  }

  .header-actions {
    width: 100%;
    overflow-x: auto;
    gap: 8px;
    scrollbar-width: none;
    -webkit-overflow-scrolling: touch;
  }

  .nav-btn,
  .header-actions .btn-save {
    flex-shrink: 0;
    white-space: nowrap;
  }
}
```

### 2. Applied Locations
**Problem:** Absolute-positioned bubbles overlapped on narrow screens.

**Solution:**
- ≤640px: chip/tag wrap layout (counts always visible)
- 641–768px: keep glass bubbles, slightly smaller radii
- Desktop: unchanged collision-avoidance bubbles

```css
@media (max-width: 640px) {
  .loc-bubbles {
    position: relative;
    height: auto;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    padding: 12px;
  }

  .loc-bubble {
    position: relative !important;
    width: auto;
    height: auto;
    border-radius: 20px;
    padding: 8px 14px;
  }
}
```

### 3. Daily Activity Heatmap
**Problem:** The calendar grid was cut off on mobile, hiding recent days.

**Solution:**
- Horizontal scroll from the 900px breakpoint
- Auto-scroll to the most recent days on load
- Cell size stays readable; do not shrink-to-fit and clip overflow

```css
@media (max-width: 900px) {
  .cal-wrap {
    overflow-x: auto;
    overflow-y: hidden;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: thin;
  }
}
```

```javascript
setTimeout(() => {
  const calWrap = document.querySelector('.cal-wrap');
  if (calWrap && window.innerWidth <= 900) {
    calWrap.scrollLeft = calWrap.scrollWidth;
  }
}, 100);
```

### 4. Job List Pagination
**Problem:** Loading every job at once was slow on mobile.

**Solution:**
- Server-side pagination (20 jobs per page) on `/api/jobs`
- Prev/Next controls and page indicator
- Reset to page 1 when filters or search change

### 5. Funnel and Journey Spacing
**Problem:** Funnel + Application Journey used too much vertical space on phones.

**Solution:** Tighter padding at 768px (funnel 8px/6px gap, journey 8px/6px/4px)
without changing desktop spacing.

## Testing

1. **Desktop (≥1024px):** header in one row, location bubbles, inline heatmap, pagination at the bottom when needed.
2. **Tablet (640–900px):** stacked header, scrollable heatmap, location bubbles (slightly smaller).
3. **Phone (≤640px):** scrollable header buttons, location chips, heatmap scrolled to recent days, pagination. Check 390×844 and 360×740.

See `TESTING_REPORT.md` for the PR #2 visual check, and `TESTING_GUIDE.md` for manual steps.

## Files Changed

- `src/web/web_app.py`: pagination on `/api/jobs`
- `templates/index.html`: mobile CSS + pagination UI/logic
