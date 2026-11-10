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

**Solution:** Keep the desktop glass-bubble layout on every width. On
phones, use a shorter canvas and smaller radii so bubbles still pack
instead of turning into a chip list.

```javascript
const isMobile = window.innerWidth <= 768;
const minR = isMobile ? 16 : 22;
const maxR = isMobile ? Math.min(38, ...) : Math.min(58, ...);
```

### 3. Daily Activity Heatmap
**Problem:** The calendar grid was either clipped or left as a narrow
block that did not match page width.

**Solution:** On stacked layouts (≤900px), keep the heatmap and the four
daily metric cards on one row. Cell size is measured to the leftover width
and capped so the grid stays compact.

```javascript
function sizeHeatmapToWidth() {
  const available = wrap.clientWidth - dowWidth - wrapGap;
  const cell = Math.floor((available - (weekCount - 1) * gap) / weekCount);
  section.style.setProperty('--cal-cell', `${cell}px`);
}
```

### 4. Job List Pagination
**Problem:** Loading every job at once was slow on mobile.

**Solution:**
- Server-side pagination (20 jobs per page) on `/api/jobs`
- Prev/Next controls and page indicator
- Reset to page 1 when filters or search change

### 5. Funnel and Journey Spacing
**Problem:** Funnel + Application Journey used too much vertical space on phones.

**Solution:** Funnel rows use 2px padding and no gap between steps.
Journey tracks drop extra padding (`min-height: 15px`, plot `14px`) so
vertical spacing stays tight. Desktop spacing is unchanged.

## Testing

1. **Desktop (≥1024px):** header in one row, location bubbles, inline heatmap, pagination at the bottom when needed.
2. **Tablet (640–900px):** stacked header, heatmap stretched to page width, smaller location bubbles.
3. **Phone (≤640px):** scrollable header buttons, compact funnel/journey, small location bubbles, full-width heatmap. Check 390×844 and 360×740.

See `TESTING_REPORT.md` for the PR #2 visual check, and `TESTING_GUIDE.md` for manual steps.

## Files Changed

- `src/web/web_app.py`: pagination on `/api/jobs`
- `templates/index.html`: mobile CSS + pagination UI/logic
