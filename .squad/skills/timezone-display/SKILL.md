---
name: "timezone-display"
description: "Client-side timezone-aware datetime formatting with dual timezone support"
domain: "frontend, datetime, i18n"
confidence: "high"
source: "earned"
tools:
  - name: "JavaScript Intl API"
    description: "Browser-native internationalization API for formatting dates/times"
    when: "Formatting timestamps for display in web UI"
---

## Context
When displaying timestamps in a web dashboard where:
- Backend operates in a configured timezone (e.g., scheduler runs in UTC)
- Users may be in different timezones globally
- Need to show times clearly without confusion

This pattern provides dual-timezone display: primary (configured TZ) + secondary (user's local TZ when different).

## Patterns

### Backend Contract
Backend provides:
```python
{
    "timestamp": "2024-03-20 14:30:00 UTC",  # Human-readable fallback
    "timestamp_iso": "2024-03-20T14:30:00+00:00",  # ISO 8601 with timezone
    "timezone": "UTC"  # IANA timezone name
}
```

### Frontend Implementation
```javascript
(function() {
    const timestampIso = "{{ timestamp_iso }}";
    const configuredTz = "{{ timezone }}";
    
    function formatDateTime(isoStr, displayId) {
        if (!isoStr) return;
        
        try {
            const dt = new Date(isoStr);
            if (isNaN(dt.getTime())) return;
            
            const displayEl = document.getElementById(displayId);
            if (!displayEl) return;
            
            const options = {
                year: 'numeric',
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                timeZoneName: 'short'
            };
            
            try {
                // Format in configured timezone
                const formatted = dt.toLocaleString('en-US', {
                    ...options, 
                    timeZone: configuredTz
                });
                
                // Check if user's timezone differs
                const userTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
                if (userTz !== configuredTz) {
                    const localFormatted = dt.toLocaleString('en-US', options);
                    displayEl.innerHTML = formatted + 
                        '<br><small style="color: #888;">(' + localFormatted + ')</small>';
                    displayEl.title = 'Configured: ' + formatted + 
                        '\nYour local: ' + localFormatted;
                } else {
                    displayEl.textContent = formatted;
                }
            } catch (e) {
                // Fallback if timezone not supported
                const formatted = dt.toLocaleString('en-US', options);
                displayEl.textContent = formatted;
            }
        } catch (e) {
            console.error('Error formatting datetime:', e);
        }
    }
    
    formatDateTime(timestampIso, 'timestamp-display');
})();
```

### HTML Template
```html
<span>Last run: <strong id="timestamp-display">{{ timestamp or "Never" }}</strong></span>

<script>
    /* Include formatDateTime function here */
</script>
```

## Examples

### Dashboard scheduler times
```html
<div class="scheduler-bar">
    <span>Last run: <strong id="last-run-display">{{ last_run }}</strong></span>
    <span>Next run: <strong id="next-run-display">{{ next_run }}</strong></span>
</div>
<script>
    formatDateTime("{{ last_run_iso }}", 'last-run-display');
    formatDateTime("{{ next_run_iso }}", 'next-run-display');
</script>
```

**Result when user TZ ≠ configured TZ**:
```
Last run: Mar 20, 2024, 02:30:00 PM EDT
          (Mar 20, 2024, 08:30:00 PM CEST)
```

**Result when user TZ = configured TZ**:
```
Last run: Mar 20, 2024, 02:30:00 PM EDT
```

## Anti-Patterns

### ❌ Server-side timezone conversion to user timezone
- Backend can't know user's timezone reliably
- Requires session management or cookies
- Doesn't handle users changing timezones

### ❌ External timezone libraries client-side
- Moment.js, date-fns-tz add unnecessary bundle size
- Native Intl API is well-supported (IE11+)
- No maintenance burden

### ❌ Showing only user's local time
- Loses context of when scheduler actually operates
- Confusing when collaborating with team in different TZ

### ❌ Inline timezone conversion logic
- Duplicate code across multiple pages
- Hard to maintain consistent formatting
- Extract to shared function or utility module

## Browser Support
- Intl API: Chrome 24+, Firefox 29+, Safari 10+, Edge 12+
- timeZone parameter: Chrome 24+, Firefox 52+, Safari 10+, Edge 14+
- Graceful fallback for older browsers shows browser's default format

## Related Files
- `web/templates/dashboard.html` - First implementation
- `web/app.py` - Backend timezone handling with pytz
