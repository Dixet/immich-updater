# Immich Metadata Updater

A Flask-based web application for managing Immich asset metadata. This tool helps you find assets with date mismatches, extract dates from filenames using regex patterns, and update asset metadata in bulk. It automatically removes search tags after updating assets to keep your library organized.

## Features

### Core Functionalities

1. **Asset Search & Filtering**
   - Search assets by tag name (e.g., "toupdate" to find assets that need date correction)
   - Filter by filename substring
   - Limit results for performance
   - Filter results to show only assets that match filename patterns

2. **Date Extraction from Filenames**
   - Define custom regex patterns to extract dates from filenames
   - Support for multiple date formats (date-only or date+time)
   - Patterns can include named groups (year/month/day/hour/minute/second) or use strptime format strings
   - Patterns are loaded from `.env` configuration or can be edited in the web UI

3. **Smart Date Merging**
   - When filename only contains date (no time), preserves the time component from the asset's current datetime
   - Example: Filename `IMG-20230414-WA0012.jpg` → Date `2023-04-14` + Current time `15:18:54` = `2023-04-14T15:18:54.000Z`

4. **Bulk Metadata Updates**
   - Update `dateTimeOriginal`, `fileCreatedAt`, and `fileModifiedAt` for multiple assets
   - Update all visible assets or selected assets
   - Automatic tag removal: Removes the search tag (e.g., "toupdate") after successful update
   - Error handling: Continues processing even if individual updates fail

5. **Pattern Management**
   - Load patterns from `.env` file
   - Edit patterns directly in the web UI
   - Patterns support inline format hints: `regex::fmt=%Y%m%d`

## Setup

### Prerequisites

- Python 3.10 or higher
- Access to an Immich instance
- Immich API key with `asset.update` permission

### Installation

1. **Clone or download this repository**

2. **Create a virtual environment:**
   ```bash
   python -m venv .venv
   ```

3. **Activate the virtual environment:**
   - Windows PowerShell: `.venv\Scripts\Activate.ps1`
   - Windows CMD: `.venv\Scripts\activate.bat`
   - Linux/Mac: `source .venv/bin/activate`

4. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

5. **Configure environment variables:**
   ```bash
   cp env.example .env
   ```
   Edit `.env` with your Immich configuration (see [Environment Variables](#environment-variables) below).

## Running the Application

```bash
python app.py
```

The application will start on `http://localhost:5000` (or the port specified in `PORT` environment variable).

Open your browser and navigate to `http://localhost:5000` to access the web interface.

## Environment Variables

All configuration is done through the `.env` file. Here's a complete reference:

### Required Variables

#### `IMMICH_BASE_URL`
- **Description**: The base URL of your Immich server
- **Format**: Full URL including protocol (http:// or https://)
- **Example**: `http://localhost:2283` or `https://immich.example.com`
- **Note**: Do not include trailing slash

#### `IMMICH_API_KEY`
- **Description**: Your Immich API key with `asset.update` permission
- **Format**: UUID string
- **Example**: `INkWgA2R9KyjHVt5o7s5JkGpGewo6NB7D1OY0MybAA`
- **How to get**: Generate from Immich Admin Settings → API Keys

### Optional Variables

#### `IMMICH_VERIFY_SSL`
- **Description**: Whether to verify SSL certificates when connecting to Immich
- **Default**: `1` (verify SSL)
- **Values**: 
  - `1`, `true`, `True` - Verify SSL (recommended for production)
  - `0`, `false`, `False` - Skip SSL verification (useful for self-signed certificates)
- **Example**: `IMMICH_VERIFY_SSL=0`

#### `DEFAULT_TAG_NAME`
- **Description**: Default tag name to use when searching assets
- **Default**: Empty (no default tag)
- **Example**: `DEFAULT_TAG_NAME=toupdate`
- **Usage**: When you load the search page, this tag will be pre-filled in the search form

#### `FILENAME_PATTERNS_JSON`
- **Description**: JSON array of regex patterns for extracting dates from filenames
- **Format**: JSON array of `[regex_pattern, strftime_format]` pairs
- **Important**: Must be on a single line (python-dotenv doesn't support multiline values)
- **Example**: 
  ```json
  [["\\d+-IMG-(\\d{8})-WA\\d+","%Y%m%d"],["IMG-(\\d{8})-WA\\d+","%Y%m%d"],["IMG_(\\d{8})_(\\d{6})","%Y%m%d%H%M%S"],["VID-(\\d{8})-WA\\d+","%Y%m%d"],["(20\\d{2})(\\d{2})(\\d{2})[_-]","%Y%m%d"]]
  ```
- **Pattern Format**:
  - First element: Regex pattern (Python regex syntax)
  - Second element: strptime format string (optional, for parsing date from captured groups)
  - Patterns can also use named groups: `(?<year>\d{4})(?<month>\d{2})(?<day>\d{2})`
- **Pattern Matching**:
  - Patterns are tried in order (first match wins)
  - If pattern has named groups (year/month/day), they're used directly
  - If pattern has format string, it's applied to the first capturing group
  - Patterns can be edited in the web UI with `::fmt=` syntax: `regex::fmt=%Y%m%d`

#### `PORT`
- **Description**: Port number for the Flask web server
- **Default**: `5000`
- **Example**: `PORT=8080`

#### `FLASK_SECRET_KEY`
- **Description**: Secret key for Flask session management
- **Default**: Not set (Flask will generate a temporary one)
- **Example**: `FLASK_SECRET_KEY=your-secret-key-here`
- **Note**: For production, use a strong random secret key

### Example `.env` File

```bash
# Immich API Configuration (Required)
IMMICH_BASE_URL=http://fedora:2283
IMMICH_API_KEY=INkWgA2R9KyjHVt5o7s5JkGpGewo6NB7D1OY0MybAA

# SSL Verification (Optional)
IMMICH_VERIFY_SSL=0

# Default Search Tag (Optional)
DEFAULT_TAG_NAME=toupdate

# Filename Patterns (Optional)
# Format: JSON array of [regex, strftime_format] pairs
# MUST be on a single line
FILENAME_PATTERNS_JSON=[["\\d+-IMG-(\\d{8})-WA\\d+","%Y%m%d"],["IMG-(\\d{8})-WA\\d+","%Y%m%d"],["IMG_(\\d{8})_(\\d{6})","%Y%m%d%H%M%S"],["VID-(\\d{8})-WA\\d+","%Y%m%d"],["(20\\d{2})(\\d{2})(\\d{2})[_-]","%Y%m%d"]]

# Flask Configuration (Optional)
FLASK_SECRET_KEY=change-me-to-something-secure
PORT=5000
```

## Usage

### Basic Workflow

1. **Tag Your Assets**: In Immich, tag assets that need date correction (e.g., tag them with "toupdate")

2. **Search Assets**: 
   - Open the web interface
   - Enter the tag name (or use `DEFAULT_TAG_NAME` from `.env`)
   - Optionally filter by filename substring
   - Click "Search"

3. **Configure Patterns** (if not already in `.env`):
   - Scroll to the "Patterns" section
   - Enter regex patterns (one per line)
   - Use `::fmt=%Y%m%d` syntax to specify date format
   - Click "Filter" to see only assets that match patterns

4. **Review Results**:
   - The table shows:
     - **ID**: Asset UUID
     - **Filename**: Original filename
     - **Current datetime**: Current metadata datetime
     - **Guessed datetime**: Date extracted from filename (with time preserved if filename only has date)

5. **Update Assets**:
   - **Single Update**: Click "Update" button next to an asset
   - **Bulk Update**: Click "Update ALL" to update all visible assets
   - After update, the search tag is automatically removed

### Pattern Examples

#### Pattern 1: WhatsApp Images
```
\d+-IMG-(\d{8})-WA\d+::fmt=%Y%m%d
```
- Matches: `1736223541-IMG-20230414-WA0012.jpg`
- Extracts: `20230414` → `2023-04-14`

#### Pattern 2: Date Only (YYYYMMDD)
```
IMG-(\d{8})-WA\d+::fmt=%Y%m%d
```
- Matches: `IMG-20230414-WA0012.jpg`
- Extracts: `20230414` → `2023-04-14`

#### Pattern 3: Date + Time (Named Groups)
```
IMG_(?<year>\d{4})(?<month>\d{2})(?<day>\d{2})_(?<hour>\d{2})(?<minute>\d{2})(?<second>\d{2})
```
- Matches: `IMG_20230414_151854.jpg`
- Extracts: `2023-04-14T15:18:54`

#### Pattern 4: Date Only (YYYY-MM-DD)
```
(20\d{2})-(\d{2})-(\d{2})
```
- Matches: `2023-04-14-photo.jpg`
- Note: Requires named groups or format string for proper parsing

## How It Works

### Date Extraction Process

1. **Pattern Matching**: Each filename is tested against patterns in order
2. **Date Parsing**: 
   - If pattern has named groups (year/month/day/hour/minute/second), they're used directly
   - If pattern has a format string (`::fmt=`), it's applied to the first capturing group
   - If pattern has both, named groups take precedence
3. **Time Merging**: 
   - If extracted date has no time (00:00:00), the time from the asset's current datetime is preserved
   - Example: Filename date `2023-04-14` + Current time `15:18:54` = `2023-04-14T15:18:54.000Z`

### Update Process

1. **Search**: Assets are retrieved by tag using Immich search API
2. **Pattern Matching**: Filenames are matched against configured patterns
3. **Date Extraction**: Dates are extracted and merged with current time if needed
4. **Update**: Asset metadata is updated via PUT `/api/assets/{id}` endpoint
5. **Tag Removal**: Search tag is removed via DELETE `/api/tags/{tagId}/assets` endpoint

### API Endpoints Used

- `GET /api/tags` - List all tags
- `POST /api/search/metadata` - Search assets by tag
- `PUT /api/assets/{id}` - Update asset metadata
- `DELETE /api/tags/{tagId}/assets` - Remove tag from assets

## Troubleshooting

### Patterns Not Matching

- Check regex syntax (use Python regex)
- Verify capture groups are correct
- Test patterns in the web UI by entering them manually
- Use "Filter" button to see only matching assets

### SSL Certificate Errors

- Set `IMMICH_VERIFY_SSL=0` for self-signed certificates
- Only use this in development/trusted environments

### Tag Removal Failing

- Verify API key has `asset.update` permission
- Check Immich server logs for errors
- Tag removal errors are logged but don't fail the update

### Date Not Preserving Time

- Ensure the pattern extracts only date (no time components)
- The merge only happens when guessed datetime is `00:00:00`
- If pattern extracts time, it will be used as-is

## Notes

- Patterns defined in `.env` are loaded automatically on startup
- Patterns can be edited in the web UI and persist in URL parameters
- The search tag is automatically removed after successful update
- Updates are done in bulk, so large batches may take time
- Errors are logged but don't stop the update process

## License

This project is provided as-is for managing Immich asset metadata.
