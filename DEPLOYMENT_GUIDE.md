# Portfolio War Room v5.0 - Deployment Guide

## 🚀 Quick Deploy to GitHub + Streamlit Cloud

### Step 1: Prepare Files

You have 3 files to deploy:
1. `app_enhanced.py` - Main application
2. `requirements.txt` - Dependencies
3. `README_v5.md` - Documentation

### Step 2: Push to GitHub

**Option A: Using Git Command Line**

```bash
# Navigate to your repo
cd /path/to/my-portfolio-ai

# Replace existing app.py with enhanced version
mv app_enhanced.py app.py

# Update README
mv README_v5.md README.md

# Add files
git add app.py requirements.txt README.md

# Commit
git commit -m "v5.0: Enhanced mobile-responsive UI with clickable cards"

# Push to GitHub
git push origin main
```

**Option B: Using GitHub Web Interface**

1. Go to https://github.com/prashanthkrishnan91/my-portfolio-ai
2. Click "Add file" → "Upload files"
3. Drag and drop:
   - `app_enhanced.py` (rename to `app.py` after upload)
   - `requirements.txt`
   - `README_v5.md` (rename to `README.md` after upload)
4. Commit with message: "v5.0 Enhanced Edition"

### Step 3: Deploy on Streamlit Cloud

1. **Go to Streamlit Cloud**
   - Visit: https://share.streamlit.io
   - Sign in with GitHub

2. **Create New App**
   - Click "New app"
   - Repository: `prashanthkrishnan91/my-portfolio-ai`
   - Branch: `main`
   - Main file path: `app.py`
   - App URL: Choose custom name (e.g., `portfolio-war-room`)

3. **Click "Deploy"**
   - Initial deployment: ~2-3 minutes
   - Your app will be live at: `https://portfolio-war-room.streamlit.app`

4. **Bookmark & Share**
   - Works on desktop and mobile browsers
   - No login required for viewing
   - Updates automatically when you push to GitHub

### Step 4: First-Time App Setup

Once deployed:

1. **Refresh Prices**
   - Click "🔄 REFRESH PRICES" button
   - Wait 5-10 seconds for all 41 positions
   - Verify prices loaded correctly

2. **Import Latest CSV**
   - Download latest Robinhood CSV
   - Go to "Import Data" tab
   - Upload CSV file
   - Review transactions
   - Click "Confirm & Apply Updates"

3. **Verify Holdings**
   - Go to "Holdings" tab
   - Confirm all positions match Robinhood
   - Check that SELL items appear correctly

4. **Bookmark for Daily Use**
   - Add to home screen on mobile
   - Bookmark in browser on desktop

## 📱 Mobile Access

### iPhone/iPad Safari
1. Open app in Safari
2. Tap Share button (box with arrow)
3. Tap "Add to Home Screen"
4. Name it "Portfolio War Room"
5. Icon appears on home screen

### Android Chrome
1. Open app in Chrome
2. Tap menu (3 dots)
3. Tap "Add to Home screen"
4. Name it "Portfolio War Room"
5. Icon appears on home screen

## 🔄 Updating the App

Whenever you want to make changes:

```bash
# Make edits to app.py
# Then:
git add app.py
git commit -m "Description of changes"
git push origin main

# Streamlit Cloud auto-detects changes and redeploys in ~30 seconds
```

## 🎯 Daily Usage Flow

### Morning Routine
1. Open app (bookmark or home screen icon)
2. Click "🔄 REFRESH PRICES"
3. Check "Overview" tab for alerts
4. Execute any urgent SELL or TRIM actions

### Friday (Deposit Day)
1. Open "Deploy $900" tab
2. Note recommended purchases
3. Execute trades in Robinhood app
4. Return to Portfolio War Room
5. Click "✓ Log This Deposit"
6. Done!

### After Trades
1. Download Robinhood CSV
2. Go to "Import Data" tab
3. Upload CSV
4. Confirm updates
5. Verify in "Holdings" tab

## 🔧 Troubleshooting

### Prices Not Updating
- Check internet connection
- Try refreshing browser
- yfinance may be down (rare) - wait 5 minutes

### CSV Import Errors
- Ensure CSV is from Robinhood (not manually edited)
- Check file encoding is UTF-8
- Try re-downloading from Robinhood

### App Not Loading
- Check Streamlit Cloud status
- Try clearing browser cache
- Force refresh: Ctrl+Shift+R (PC) or Cmd+Shift+R (Mac)

### Mobile Display Issues
- Rotate to portrait mode
- Zoom out if text is too large
- Update browser to latest version

## 📊 Performance Tips

### Fast Price Updates
- Refresh during market hours for fastest response
- After hours: may take 10-15 seconds

### Smooth Mobile Experience
- Use Chrome or Safari (best performance)
- Close other tabs to free memory
- Update iOS/Android to latest version

## 🔐 Privacy & Security

- App runs on Streamlit's cloud (secure)
- No data stored between sessions
- CSV uploads processed in-browser only
- Export JSON to keep local backups
- Never share your Streamlit app URL publicly if you don't want others to see your portfolio

## 📞 Getting Help

If you encounter issues:

1. Check logs in Streamlit Cloud:
   - Go to app dashboard
   - Click "Manage app"
   - View logs for errors

2. Common fixes:
   - Redeploy app (hamburger menu → "Reboot app")
   - Check requirements.txt has all dependencies
   - Verify app.py is valid Python

3. GitHub Issues:
   - Create issue in your repo with error message
   - Include screenshot if possible

---

**Ready to deploy?** Follow Steps 1-4 above and you'll be live in ~5 minutes! 🚀
