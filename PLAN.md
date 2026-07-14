# Implementation Plan for ZKTeco Web UI Enhancements

## Overview
Based on the code review, several features need to be implemented or enhanced:

## 1. Manual Attendance Feature (Backend Routes Added ✓)

### Issue
- The `manual_attendance.html` template exists but no corresponding routes were defined in `web/app.py`
- Manual attendance entries are not reflected in monthly reports

### Solution Completed
Added the missing routes and backend logic for manual attendance in `web/app.py`:

#### Routes Implemented in web/app.py:
- `GET /manual-attendance` - Display manual attendance form with filters
- `POST /manual-attendance/add` - Add a single manual attendance entry
- `POST /manual-attendance/upload` - Upload Excel file for bulk manual attendance
- `POST /manual-attendance/{id}/delete` - Delete a manual attendance entry
- `GET /manual-attendance/sample` - Download sample Excel template

#### Database Integration:
✅ Utilized existing database functions:
- `add_manual_attendance_entry()` - for storing manual entries
- `get_manual_attendance()` - for retrieving manual entries  
- `delete_manual_attendance()` - for deleting manual entries
- All entries are stored in the `attendance_logs` table with `source='manual'`

#### Monthly Report Integration:
⏳ Manual entries are already included in reports since the `get_manual_attendance()` function is used by the reports module, but we should verify this works correctly.

## 2. Nepali Calendar Integration

### Issue
- Need to show both English and Nepali dates in UI for manual attendance
- Need to provide date conversion capabilities

### Solution
✅ Leveraged existing `nepali_utils` module which is already imported and registered with templates:
- Used `ad_to_bs()` function for conversions in the manual attendance routes
- The template already has capability to display both date formats (to be verified)

## 3. User Profile Enhancement

### Issue
- User profile currently shows limited information
- Need to allow users to edit their additional details

### Solution
⏳ Enhance the profile functionality:
- Add fields for: Full Name, Card Number, Bank Account Number, Email, Phone, Organisation, Department, Section, Unit, Default Shift
- Implement both viewing and editing capabilities
- Ensure proper validation and data storage

## 4. Leave-Holiday Integration

### Issue
- Leave data should feed into holiday report generation

### Solution
⏳ Verify and enhance the leave processing logic to ensure:
- Approved leave applications are properly considered in holiday calculations
- Holiday reports accurately reflect leave data

## Implementation Approach

### Completed:
### Phase 1: Manual Attendance Backend
1. ✅ Added missing routes to web/app.py
2. ✅ Integrated with existing database storage/retrieval for manual entries
3. ✅ Ensured integration with existing attendance_logs table
4. ⏳ Test basic functionality (needs verification)

### Phase 2: UI Enhancements
1. ⏳ Enhance manual attendance form with Nepali/English date display
2. ⏳ Add date conversion widgets/utility
3. ⏳ Enhance user profile with additional fields
4. ⏳ Ensure proper data binding and validation

### Phase 3: Integration & Reporting
1. ⏳ Verify manual entries appear in monthly reports
2. ⏳ Test leave-holiday integration
3. ⏳ Validate all calculations and displays

### Phase 4: Device Push Functionality (Optional)
1. ⏳ Implement ability to push manual attendance to devices
2. ⏳ Add device selection UI
3. ⏳ Implement sync logic

## Files Modified
1. ✅ `web/app.py` - Added routes and backend logic for manual attendance

## Files to Modify
2. `web/templates/manual_attendance.html` - May need minor enhancements for Nepali date display
3. `web/templates/profile.html` - Enhance for additional fields
4. Possibly `db.py` - Add helper functions if needed
5. `web/helpers.py` - Add utility functions if needed

## Dependencies
- Existing `nepali_utils` module for date conversions
- Existing database schema (attendance_logs table)
- Existing template infrastructure