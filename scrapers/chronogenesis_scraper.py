"""
ChronoGenesis website scraper using Selenium
"""
from typing import Dict, List, Optional
import logging
import asyncio
from functools import partial

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

from scrapers.base_scraper import BaseScraper
from config.settings import SCRAPE_TIMEOUT

logger = logging.getLogger(__name__)


class ChronoGenesisScraper(BaseScraper):
    """Scraper for ChronoGenesis.net club profile pages"""
    
    def __init__(self, url: str):
        super().__init__(url)
        self.current_day_count = 1
    
    def _get_chrome_version(self) -> str:
        """Detect the installed Chrome/Chromium version across platforms"""
        import subprocess
        import re
        import platform
        
        system = platform.system()
        commands = []
        
        if system == "Windows":
            commands = [
                r'reg query "HKEY_CURRENT_USER\Software\Google\Chrome\BLBeacon" /v version',
                r'reg query "HKLM\SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Google Chrome" /v version',
            ]
        elif system == "Darwin":
            commands = [
                ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--version'],
                ['chromium', '--version'],
            ]
        else:
            commands = [
                ['chromium-browser', '--version'],
                ['chromium', '--version'],
                ['google-chrome', '--version'],
            ]
        
        for cmd in commands:
            try:
                if isinstance(cmd, str):
                    result = subprocess.run(cmd, capture_output=True, text=True, 
                                          timeout=5, shell=True)
                else:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                
                version_output = result.stdout
                match = re.search(r'(\d+\.\d+\.\d+\.\d+)', version_output)
                if match:
                    version = match.group(1)
                    logger.info(f"Detected Chrome version: {version}")
                    return version
            except Exception as e:
                logger.debug(f"Command failed: {cmd}, error: {e}")
                continue
        
        logger.warning("Could not detect Chrome version, using fallback 131.0.0.0")
        return "131.0.0.0"
    
    def _setup_driver(self) -> webdriver.Chrome:
        """Set up Selenium Chrome driver with headless options"""
        import platform
        
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        
        # Anti-detection arguments
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        system = platform.system()
        machine = platform.machine().lower()
        
        # Platform-specific arguments
        if system == "Linux":
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            
            if 'arm' in machine or 'aarch64' in machine:
                chrome_options.add_argument("--no-zygote")
                chrome_options.add_argument("--single-process")
        
        chrome_options.add_argument("--disable-software-rasterizer")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--lang=en-US")
        
        # Detect Chrome version and set matching user agent
        chrome_version = self._get_chrome_version()
        logger.info(f"Detected Chrome version: {chrome_version}")
        logger.info(f"Detected OS: {system}, CPU architecture: {machine}")
        
        # Set appropriate user agent based on platform
        if system == "Windows":
            user_agent = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_version} Safari/537.36"
        elif system == "Darwin":
            user_agent = f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_version} Safari/537.36"
        elif 'arm' in machine or 'aarch64' in machine:
            user_agent = f"Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_version} Safari/537.36"
        else:
            user_agent = f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_version} Safari/537.36"
        
        chrome_options.add_argument(f"--user-agent={user_agent}")
        logger.info(f"Using user agent: {user_agent}")
        
        # ChromeDriver selection based on platform
        if system == "Linux":
            import os
            chromedriver_path = '/usr/bin/chromedriver'
            
            if os.path.exists(chromedriver_path):
                logger.info(f"Using system ChromeDriver at {chromedriver_path}")
                service = Service(chromedriver_path)
            else:
                logger.error(f"ChromeDriver not found at {chromedriver_path}")
                logger.error("Install it with: apt-get install chromium-driver")
                raise FileNotFoundError(
                    f"ChromeDriver not found. Please install chromium-driver:\n"
                    f"  apt-get update\n"
                    f"  apt-get install chromium chromium-driver"
                )
        else:
            logger.info(f"Using webdriver-manager for {system}")
            try:
                service = Service(ChromeDriverManager().install())
            except Exception as e:
                logger.error(f"Failed to install ChromeDriver via webdriver-manager: {e}")
                logger.error("Please ensure Chrome/Chromium is installed and up to date")
                raise
        
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # Hide webdriver property
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': '''
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                })
            '''
        })
        
        return driver
    
    def _handle_cookie_consent(self, driver):
        """Handle cookie consent popup"""
        try:
            logger.info("Checking for cookie consent popup...")
            import time
            time.sleep(3)
            
            selectors = [
                "//button[contains(text(), 'Continue with Recommended Cookies')]",
                "button.ez-accept-all",
                "#ez-cookie-dialog-wrapper button",
                ".ez-main-cmp-wrapper button",
                "button.fc-cta-consent",
                "button[class*='consent']",
                "button[title='Consent']",
                "//button[contains(text(), 'Consent')]",
                "//button[contains(text(), 'Accept')]",
            ]
            
            consent_clicked = False
            for selector in selectors:
                try:
                    if selector.startswith("//"):
                        consent_button = driver.find_element(By.XPATH, selector)
                    else:
                        consent_button = driver.find_element(By.CSS_SELECTOR, selector)
                    
                    if consent_button.is_displayed():
                        consent_button.click()
                        logger.info(f"Clicked consent button: {selector}")
                        consent_clicked = True
                        time.sleep(8)
                        return True
                except Exception as e:
                    logger.debug(f"Selector {selector} failed: {e}")
                    continue
            
            if not consent_clicked:
                logger.warning("Could not find or click cookie consent button")
                logger.warning("Attempting to remove cookie dialog with JavaScript...")
                
                try:
                    driver.execute_script("""
                        var ezDialog = document.getElementById('ez-cookie-dialog-wrapper');
                        if (ezDialog) ezDialog.remove();
                        
                        var overlays = document.querySelectorAll('[id*="cookie"], [class*="cookie"], [id*="consent"], [class*="consent"]');
                        overlays.forEach(function(el) {
                            if (el.style.position === 'fixed' || el.style.zIndex > 100) {
                                el.remove();
                            }
                        });
                    """)
                    logger.info("Removed cookie dialog with JavaScript")
                    time.sleep(2)
                    return True
                except Exception as js_error:
                    logger.error(f"JavaScript removal failed: {js_error}")
                    return False
                    
        except Exception as e:
            logger.warning(f"Cookie consent handling failed: {e}")
            return False
    
    def _scrape_sync(self) -> Dict[str, Dict]:
        """Synchronous scraping function (runs in executor)"""
        driver = None
        try:
            driver = self._setup_driver()
            
            import time
            
            # Step 1: Visit homepage
            logger.info("Loading ChronoGenesis homepage...")
            driver.get("https://chronogenesis.net")
            time.sleep(3)
            
            # Verify we're on the right site
            current_url = driver.current_url
            logger.info(f"Homepage loaded: {current_url}")
            
            if "chronogenesis.net" not in current_url:
                logger.error(f"Unexpected URL: {current_url}")
                driver.save_screenshot("debug_wrong_url.png")
                raise ValueError(f"Page redirected to unexpected URL: {current_url}")
            
            # Step 2: Handle cookie consent
            self._handle_cookie_consent(driver)
            
            # Step 3: Click "Club Profile" card to get to club search page
            logger.info("Looking for 'Club Profile' card on homepage...")
            club_profile_clicked = False
            
            # Wait a moment for the page to fully render
            time.sleep(3)
            
            # Try multiple methods to click the Club Profile card
            click_methods = [
                # Try clicking the card text directly
                lambda: driver.find_element(By.XPATH, "//*[contains(text(), 'Club Profile')]").click(),
                
                # Try clicking the card that contains "Look up your club"
                lambda: driver.find_element(By.XPATH, "//*[contains(text(), 'Look up your club')]").click(),
                
                # Try clicking the card with both Club Profile AND Look up text
                lambda: driver.find_element(By.XPATH, "//div[contains(., 'Club Profile') and contains(., 'Look up')]").click(),
                
                # Try the Club nav link at the top as fallback
                lambda: driver.find_element(By.LINK_TEXT, "Club").click(),
                
                # Try clicking any clickable element with "Club" text
                lambda: driver.find_element(By.XPATH, "//span[text()='Club']").click(),
            ]
            
            for idx, method in enumerate(click_methods):
                try:
                    logger.info(f"Trying click method {idx + 1}...")
                    method()
                    logger.info("Successfully clicked Club Profile!")
                    club_profile_clicked = True
                    time.sleep(5)  # Wait for navigation
                    break
                except Exception as e:
                    logger.debug(f"Click method {idx + 1} failed: {e}")
                    continue
            
            if not club_profile_clicked:
                logger.warning("Could not click Club Profile, trying direct navigation")
                driver.get("https://chronogenesis.net/club_profile")
                time.sleep(5)
            
            # Step 4: We should now be on the club search page with input field
            logger.info(f"Current page: {driver.current_url}")
            
            # Wait for React app to fully render
            logger.info("Waiting for React app to render...")
            time.sleep(8)  # Give more time for React to render
            
            # Debug: Check what's on the page
            logger.info("Checking page content...")
            page_text = driver.find_element(By.TAG_NAME, "body").text[:500]
            logger.info(f"Page body text (first 500 chars): {page_text}")
            
            # Debug: Check for any inputs on the page
            all_inputs = driver.find_elements(By.TAG_NAME, "input")
            logger.info(f"Found {len(all_inputs)} input elements on page")
            for idx, inp in enumerate(all_inputs):
                logger.info(f"  Input {idx}: class='{inp.get_attribute('class')}', placeholder='{inp.get_attribute('placeholder')}'")
            
            # Extract circle_id from original URL
            circle_id = None
            if "circle_id=" in self.url:
                circle_id = self.url.split("circle_id=")[-1]
                logger.info(f"Extracted circle_id: {circle_id}")
            
            if not circle_id:
                logger.error("No circle_id found in URL")
                raise ValueError("Cannot proceed without circle_id")
            
            # Step 5: Wait for the input field to be present
            logger.info("Looking for club search input field...")
            
            search_input = None
            max_attempts = 3
            
            for attempt in range(1, max_attempts + 1):
                try:
                    logger.info(f"Attempt {attempt}/{max_attempts} to find input field...")
                    
                    # Try multiple selectors
                    selectors = [
                        "input.club-id-input",
                        "input[placeholder*='Club']",
                        "input[placeholder*='ID']",
                        ".club-id-input",
                        ".club-profile-main input",
                    ]
                    
                    for selector in selectors:
                        try:
                            search_input = WebDriverWait(driver, 10).until(
                                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                            )
                            logger.info(f"Found input field using selector: {selector}")
                            break
                        except:
                            logger.debug(f"Selector failed: {selector}")
                            continue
                    
                    if search_input:
                        break
                    
                    logger.warning(f"Attempt {attempt} failed, waiting 5 seconds...")
                    time.sleep(5)
                    
                except Exception as e:
                    logger.error(f"Attempt {attempt} error: {e}")
                    if attempt < max_attempts:
                        time.sleep(5)
            
            if not search_input:
                logger.error("Could not find club-id-input field after all attempts")
                driver.save_screenshot("debug_no_input.png")
                
                # Save page source for debugging
                with open("debug_page_source.html", "w", encoding="utf-8") as f:
                    f.write(driver.page_source)
                logger.error("Saved page source to debug_page_source.html")
                
                raise ValueError("Search input field not found")
            
            # Step 6: Use JavaScript to set the value and trigger events
            logger.info("Using JavaScript to enter club name and trigger search...")
            
            # Set the input value using JavaScript
            driver.execute_script("""
                var input = document.querySelector('input.club-id-input');
                input.value = arguments[0];
                
                // Trigger input event
                var event = new Event('input', { bubbles: true });
                input.dispatchEvent(event);
                
                // Trigger change event
                var changeEvent = new Event('change', { bubbles: true });
                input.dispatchEvent(changeEvent);
            """, circle_id)
            
            logger.info(f"Set club name via JavaScript: {circle_id}")
            time.sleep(2)
            
            # Try to trigger the search via Enter key using JavaScript
            driver.execute_script("""
                var input = document.querySelector('input.club-id-input');
                var event = new KeyboardEvent('keydown', {
                    key: 'Enter',
                    code: 'Enter',
                    keyCode: 13,
                    which: 13,
                    bubbles: true
                });
                input.dispatchEvent(event);
            """)
            
            logger.info("Triggered search via JavaScript")
            time.sleep(5)
            
            # Verify we're on club profile page
            current_url = driver.current_url
            logger.info(f"Final page: {current_url}")
            
            if "circle_id=" + circle_id not in current_url:
                logger.warning(f"URL doesn't contain circle_id={circle_id}, but continuing...")
            
            # Wait for page to fully render
            logger.info("Waiting for page to fully render...")
            time.sleep(5)
            
            # Wait for chart container to be present
            logger.info("Waiting for chart container to load...")
            wait = WebDriverWait(driver, SCRAPE_TIMEOUT)
            
            try:
                chart_container = wait.until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".club_daily_chart_container"))
                )
                logger.info("Found chart container")
            except TimeoutException:
                logger.error("Chart container not found after timeout")
                logger.error("Page source (first 2000 chars):")
                logger.error(driver.page_source[:2000])
                driver.save_screenshot("debug_no_chart.png")
                raise ValueError("Chart container not found on page")
            
            # Switch to "Member Cumulative Fan Count" chart
            logger.info("Switching to 'Member Cumulative Fan Count' chart...")
            try:
                chart_select = driver.find_element(By.ID, "chart")
                select = Select(chart_select)
                select.select_by_value("member_fan_cumulative")
                logger.info("Selected 'Member Cumulative Fan Count' chart")
                time.sleep(8)
            except Exception as e:
                logger.warning(f"Could not switch chart mode: {e}")
            
            # Click the "Show data" button to reveal the hidden table
            logger.info("Looking for 'Show data' button...")
            try:
                expand_button = driver.find_element(By.CSS_SELECTOR, ".expand-button")
                expand_button.click()
                logger.info("Clicked 'Show data' button")
                time.sleep(2)
            except Exception as e:
                logger.info(f"Could not find/click expand button: {e}")
            
            # Find the chart data table
            logger.info("Looking for chart data table...")
            chart_table = None
            try:
                chart_table = chart_container.find_element(By.TAG_NAME, "table")
                logger.info("Found chart data table")
            except NoSuchElementException:
                logger.warning("No table found in chart container, trying different approach...")
                
                all_tables = driver.find_elements(By.TAG_NAME, "table")
                logger.info(f"Found {len(all_tables)} total tables on page")
                
                for idx, table in enumerate(all_tables):
                    table_class = table.get_attribute("class") or ""
                    if "club-member-table" not in table_class:
                        chart_table = table
                        logger.info(f"Using table {idx} (class: {table_class}) as chart table")
                        break
            
            if not chart_table:
                logger.error("Could not find chart data table")
                driver.save_screenshot("debug_no_chart_table.png")
                raise ValueError("Chart data table not found")
            
            # Parse the chart table to get daily data
            logger.info("Parsing chart table for daily data...")
            member_data = self._parse_chart_table(chart_table)
            
            if not member_data:
                logger.error("No data extracted from chart table")
                table_html = chart_table.get_attribute('outerHTML')[:2000]
                logger.error(f"Chart table HTML: {table_html}")
                driver.save_screenshot("debug_empty_chart_data.png")
                raise ValueError("No daily data found in chart table")
            
            logger.info(f"Successfully scraped {len(member_data)} members with daily data")
            return member_data
            
        except TimeoutException:
            logger.error(f"Timeout while loading {self.url}")
            raise
        except Exception as e:
            logger.error(f"Error during scraping: {e}")
            raise
        finally:
            if driver:
                driver.quit()
    
    def _parse_chart_table(self, table) -> Dict[str, Dict]:
        """Parse the chart data table to extract daily fan counts per member"""
        member_data = {}
        
        try:
            rows = table.find_elements(By.TAG_NAME, "tr")
            logger.info(f"Chart table has {len(rows)} rows")
            
            if len(rows) < 2:
                logger.warning("Chart table has too few rows")
                return {}
            
            # First row should be headers
            header_row = rows[0]
            headers = header_row.find_elements(By.TAG_NAME, "th")
            
            if not headers:
                headers = header_row.find_elements(By.TAG_NAME, "td")
                logger.info("Using td elements as headers")
            
            # Find which columns are day columns
            day_columns = []
            for idx, header in enumerate(headers):
                header_text = header.text.strip()
                if header_text.startswith("Day "):
                    day_columns.append(idx)
            
            num_days = len(day_columns)
            if num_days == 0:
                logger.error("No 'Day X' columns found in header")
                header_texts = [h.text.strip() for h in headers]
                logger.error(f"Headers found: {header_texts}")
                
                if all(not h for h in header_texts):
                    logger.error("All headers are empty! Cookie consent popup is likely blocking the page.")
                    raise ValueError("Cookie popup is blocking page access - headers are empty")
                
                return {}
            
            logger.info(f"Found {num_days} day columns at indices: {day_columns}")
            
            # Parse data rows
            for row_idx, row in enumerate(rows[1:], start=1):
                cells = row.find_elements(By.TAG_NAME, "td")
                
                if not cells or len(cells) < 2:
                    continue
                
                # First cell contains member name and trainer ID
                first_cell = cells[0]
                
                try:
                    span = first_cell.find_element(By.TAG_NAME, "span")
                    trainer_id = span.get_attribute("title")
                    member_name = span.text.strip()
                except:
                    member_name = first_cell.text.strip()
                    trainer_id = None
                    logger.warning(f"Could not extract trainer ID for {member_name}")
                
                if not member_name or member_name == "-" or member_name == "Player":
                    continue
                
                # Extract fan counts for each day
                daily_fans = []
                for day_col_idx in day_columns:
                    if day_col_idx >= len(cells):
                        break
                    
                    cell_text = cells[day_col_idx].text.strip()
                    
                    if not cell_text or cell_text == "-":
                        daily_fans.append(0)
                        continue
                    
                    try:
                        fan_count = int(cell_text.replace(',', ''))
                        daily_fans.append(fan_count)
                    except ValueError:
                        logger.debug(f"Non-numeric cell for {member_name}: '{cell_text}', using 0")
                        daily_fans.append(0)
                
                # Detect join day
                join_day = 1
                for day_idx, fans in enumerate(daily_fans, start=1):
                    if fans > 0:
                        join_day = day_idx
                        break
                
                # Use trainer_id as key if available
                key = trainer_id if trainer_id else member_name
                
                member_data[key] = {
                    "name": member_name,
                    "trainer_id": trainer_id,
                    "fans": daily_fans,
                    "join_day": join_day
                }
                
                logger.debug(f"Parsed {member_name} (ID: {trainer_id}): {len(daily_fans)} days, joined day {join_day}")
            
            # Set the current day count
            self.current_day_count = num_days
            logger.info(f"Successfully parsed {len(member_data)} members with {num_days} days of data")
            
            return member_data
            
        except Exception as e:
            logger.error(f"Error parsing chart table: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {}
    
    async def scrape(self) -> Dict[str, Dict]:
        """Scrape the website asynchronously"""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._scrape_sync)
        return result
    
    def get_current_day(self) -> int:
        """Get the current day number"""
        return self.current_day_count