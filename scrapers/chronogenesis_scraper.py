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
        self.current_day_count = 1  # Will be updated based on scraped data
    
    def _setup_driver(self) -> webdriver.Chrome:
        """Set up Selenium Chrome driver with headless options"""
        chrome_options = Options()
        # DEBUG MODE - commented out headless so you can see what's happening
        # chrome_options.add_argument("--headless")
        
        # Anti-detection arguments
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        # Normal browser arguments
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36")
        
        service = Service(ChromeDriverManager().install())
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
    
    def _scrape_sync(self) -> Dict[str, List[int]]:
        """Synchronous scraping function (runs in executor)"""
        driver = None
        try:
            driver = self._setup_driver()
            logger.info(f"Loading {self.url}...")
            driver.get(self.url)
            
            import time
            
            # Verify page loaded
            time.sleep(2)
            current_url = driver.current_url
            logger.info(f"Page loaded, current URL: {current_url}")
            
            # Check if we got redirected or blocked
            if "chronogenesis.net" not in current_url:
                logger.error(f"Unexpected URL: {current_url}")
                driver.save_screenshot("debug_wrong_url.png")
                raise ValueError(f"Page redirected to unexpected URL: {current_url}")
            
            # Handle cookie consent popup if it exists
            logger.info("Checking for cookie consent popup...")
            try:
                time.sleep(3)  # Wait for popup to appear
                
                # Try to find and click the "Consent" button
                selectors = [
                    "button.fc-cta-consent",
                    "button[class*='consent']",
                    "button[title='Consent']",
                    "//button[contains(text(), 'Consent')]"
                ]
                for selector in selectors:
                    try:
                        if selector.startswith("//"):
                            consent_button = driver.find_element(By.XPATH, selector)
                        else:
                            consent_button = driver.find_element(By.CSS_SELECTOR, selector)
                        consent_button.click()
                        logger.info(f"Clicked consent button: {selector}")
                        time.sleep(3)
                        break
                    except:
                        continue
            except Exception as e:
                logger.info("No consent popup found or already accepted")
            
            # Wait for chart container to be present
            logger.info("Waiting for chart container to load...")
            wait = WebDriverWait(driver, SCRAPE_TIMEOUT)
            
            try:
                chart_container = wait.until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".club_daily_chart_container"))
                )
                logger.info("Found chart container")
            except TimeoutException:
                logger.error("Chart container not found")
                driver.save_screenshot("debug_no_chart.png")
                raise ValueError("Chart container not found on page")
            
            # Switch to "Member Cumulative Fan Count" chart
            logger.info("Switching to 'Member Cumulative Fan Count' chart...")
            try:
                chart_select = driver.find_element(By.ID, "chart")
                select = Select(chart_select)
                select.select_by_value("member_fan_cumulative")
                logger.info("Selected 'Member Cumulative Fan Count' chart")
                time.sleep(5)  # Wait for chart to update and render
            except Exception as e:
                logger.warning(f"Could not switch chart mode: {e}")
            
            # Click the "Show data" button to reveal the hidden table
            logger.info("Looking for 'Show data' button...")
            try:
                expand_button = driver.find_element(By.CSS_SELECTOR, ".expand-button")
                expand_button.click()
                logger.info("Clicked 'Show data' button")
                time.sleep(2)  # Wait for table to appear
            except Exception as e:
                logger.info(f"Could not find/click expand button: {e}")
                logger.info("Table might already be visible")
            
            # Find the chart data table
            logger.info("Looking for chart data table...")
            chart_table = None
            try:
                # Look for table inside the chart container
                chart_table = chart_container.find_element(By.TAG_NAME, "table")
                logger.info("Found chart data table")
            except NoSuchElementException:
                logger.warning("No table found in chart container, trying different approach...")
                
                # Try to find any table that's not the member list table
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
                # Log table HTML for debugging
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
    
    def _parse_chart_table(self, table) -> Dict[str, List[int]]:
        """Parse the chart data table to extract daily fan counts per member
        
        Table structure:
        Header: ["Player", "Day 1", "Day 2", "Day 3", ...]
        Row 1:  [Member1Name, fans_day1, fans_day2, fans_day3, ...]
        Row 2:  [Member2Name, fans_day1, fans_day2, fans_day3, ...]
        
        Returns:
            Dict with structure:
            {
                "trainer_id": {
                    "name": "TrainerName",
                    "fans": [day1_fans, day2_fans, ...],
                    "join_day": 1  # First day with non-zero fans
                }
            }
        """
        member_data = {}
        
        try:
            rows = table.find_elements(By.TAG_NAME, "tr")
            logger.info(f"Chart table has {len(rows)} rows")
            
            if len(rows) < 2:
                logger.warning("Chart table has too few rows")
                return {}
            
            # First row should be headers (Player, Day 1, Day 2, etc.)
            header_row = rows[0]
            headers = header_row.find_elements(By.TAG_NAME, "th")
            
            # If no th elements, try td elements in first row
            if not headers:
                headers = header_row.find_elements(By.TAG_NAME, "td")
                logger.info("Using td elements as headers")
            
            # Find which columns are day columns (Day 1, Day 2, etc.)
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
                return {}
            
            logger.info(f"Found {num_days} day columns at indices: {day_columns}")
            
            # Parse data rows (each row is a member)
            for row_idx, row in enumerate(rows[1:], start=1):
                cells = row.find_elements(By.TAG_NAME, "td")
                
                # Skip empty rows
                if not cells or len(cells) < 2:
                    continue
                
                # First cell contains member name and trainer ID
                # Structure: <td><span title="trainer_id">TrainerName</span></td>
                first_cell = cells[0]
                
                try:
                    # Try to find span with title attribute (trainer ID)
                    span = first_cell.find_element(By.TAG_NAME, "span")
                    trainer_id = span.get_attribute("title")
                    member_name = span.text.strip()
                except:
                    # Fallback: just get text (for members without ID in span)
                    member_name = first_cell.text.strip()
                    trainer_id = None
                    logger.warning(f"Could not extract trainer ID for {member_name}")
                
                # Skip invalid names
                if not member_name or member_name == "-" or member_name == "Player":
                    continue
                
                # Extract fan counts for each day
                daily_fans = []
                for day_col_idx in day_columns:
                    if day_col_idx >= len(cells):
                        break
                    
                    cell_text = cells[day_col_idx].text.strip()
                    
                    # Handle empty cells or dashes as 0
                    if not cell_text or cell_text == "-":
                        daily_fans.append(0)
                        continue
                    
                    try:
                        # Parse fan count (remove commas)
                        fan_count = int(cell_text.replace(',', ''))
                        daily_fans.append(fan_count)
                    except ValueError:
                        # Non-numeric cell, treat as 0
                        logger.debug(f"Non-numeric cell for {member_name}: '{cell_text}', using 0")
                        daily_fans.append(0)
                
                # Detect join day (first day with non-zero fans)
                join_day = 1  # Default to day 1
                for day_idx, fans in enumerate(daily_fans, start=1):
                    if fans > 0:
                        join_day = day_idx
                        break
                
                # Use trainer_id as key if available, otherwise use name
                key = trainer_id if trainer_id else member_name
                
                member_data[key] = {
                    "name": member_name,
                    "trainer_id": trainer_id,
                    "fans": daily_fans,
                    "join_day": join_day
                }
                
                logger.debug(f"Parsed {member_name} (ID: {trainer_id}): {len(daily_fans)} days, joined day {join_day}")
            
            # Set the current day count based on how many day columns exist
            self.current_day_count = num_days
            logger.info(f"Successfully parsed {len(member_data)} members with {num_days} days of data")
            
            return member_data
            
        except Exception as e:
            logger.error(f"Error parsing chart table: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {}
    
    async def scrape(self) -> Dict[str, Dict]:
        """
        Scrape the website asynchronously
        
        Returns:
            Dict mapping trainer_id -> member data dict:
            {
                "trainer_id": {
                    "name": "TrainerName",
                    "trainer_id": "trainer_id",
                    "fans": [day1_fans, day2_fans, ...],
                    "join_day": 1  # First day with non-zero fans
                }
            }
        """
        # Run the synchronous Selenium code in a thread pool executor
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._scrape_sync)
        return result
    
    def get_current_day(self) -> int:
        """Get the current day number"""
        return self.current_day_count