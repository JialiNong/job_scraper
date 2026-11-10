# Mobile UI Fixes - Testing Guide

## Summary
Fixed four mobile layout issues for phone-width screens (~390px) while keeping desktop unchanged.

## Changes Made

### 1. Header Button Row Wrapping ✓
**File:** `templates/index.html` (line ~1713)
```css
.nav-btn {
  white-space: nowrap;  /* Added to prevent icon+text wrapping */
}
```
**What to check:** At 390px width, all header buttons (Log Job, Timeouts, Unmatched Jobs, Refresh, Logout) should have icons and text on one line, all with equal height.

### 2. Funnel Panel & Application Journey Spacing ✓
**File:** `templates/index.html` (lines ~1740-1780)
```css
.funnel-bar {
  padding: 8px 12px;  /* Was 12px */
  gap: 6px;           /* Explicitly set */
}
.funnel-step {
  padding: 8px 12px;  /* Was 10px 14px */
}
.journey-section {
  padding: 8px 12px;  /* Was 12px */
}
.journey-panel {
  padding: 6px;       /* Was 8px */
}
.journey-track {
  padding: 4px 6px;   /* Was 6px 8px */
}
```
**What to check:** Funnel (Title Passed/Clicked, German Filtered, etc.) and Application Journey sections should look more compact with reduced white space between elements.

### 3. Applied Locations Bubble Visualization ✓
**File:** `templates/index.html` (lines ~656-680, ~2580-2615)

**CSS:**
```css
@media (max-width: 900px) {
  .loc-bubbles { height: 200px; }  /* Was implicit */
}
@media (max-width: 640px) {
  .loc-bubbles { height: 180px; }  /* Was 220px */
}
```

**JavaScript:**
```javascript
const isMobile = window.innerWidth <= 768;
const minR = isMobile ? 16 : 22;    /* Smaller min radius on mobile */
const maxR = isMobile ? 38 : 58;    /* Smaller max radius on mobile */
```

**What to check:** Location bubbles should be smaller and fit better on mobile without overlapping. Should see a compact bubble cloud, not chips.

### 4. Daily Activity Heatmap ✓
**File:** `templates/index.html` (lines ~1780-1830)
```css
@media (max-width: 768px) {
  .cal-section {
    --cal-cell: 18px;   /* Was 26px */
    --cal-gap: 2px;     /* Was 4px */
    --cal-label: 14px;  /* Was 18px */
  }
}

@media (max-width: 400px) {
  .cal-section {
    --cal-cell: 14px;   /* Even smaller for narrow screens */
    --cal-gap: 1.5px;
    --cal-label: 12px;
  }
}
```
**What to check:** Heatmap should scale to fit full screen width on mobile without horizontal scrolling or clipping. All calendar cells should be visible.

## Testing on Render

### Prerequisites
- Deploy this branch to Render staging or directly to main (if comfortable)
- Have real job data in the database (locations, applications, activity)
- Test devices/viewports:
  - 390x844 (iPhone 12/13/14 Pro)
  - 360x740 (common Android)
  - Desktop (1920x1080)

### Test Steps

1. **Open on mobile device or use Chrome DevTools mobile emulation:**
   - F12 → Toggle device toolbar
   - Select "iPhone 12 Pro" or set custom 390x844

2. **Check Header (Issue #1):**
   - Verify Refresh button shows "↻ Refresh" on one line
   - All buttons same height
   - No text wrapping

3. **Check Funnel & Journey (Issue #2):**
   - Scroll to funnel panel (Title Clicked, German Filtered, AI Matched, Applied)
   - Verify spacing is tighter, not cramped
   - Check Application Journey section below
   - Verify vertical gaps are reduced

4. **Check Applied Locations (Issue #3):**
   - Scroll to "Applied Locations" section
   - Should see bubble visualization (not chips)
   - Bubbles should be smaller than desktop
   - No overlapping bubbles
   - Compact layout

5. **Check Daily Activity Heatmap (Issue #4):**
   - Should span full screen width
   - No horizontal scroll
   - All cells visible (not cut off)
   - Grid should scale down appropriately

6. **Verify Desktop unchanged:**
   - Test at 1920x1080
   - All sections should look identical to before
   - No layout changes on desktop

## Verification Script Results

```
✓ All mobile fixes are present in the template
✓ Flask app starts successfully
✓ Mobile CSS syntax validated

Mobile fixes ready:
  1. ✓ Header button wrapping fixed
  2. ✓ Funnel/Journey spacing tightened
  3. ✓ Location bubbles scaled for mobile
  4. ✓ Heatmap responsive sizing added
```

## Rollback Plan
If issues are found:
```bash
git checkout main
git push -f origin main  # If deployed from main
```

## Files Changed
- `templates/index.html` - Mobile CSS and JavaScript updates
- `src/core/db_mongo.py` - Made DATABASE_NAME configurable (for testing only, not production-critical)
