import streamlit as st
import pandas as pd
import os
import re
import time
import requests
import shutil
import sys
import asyncio
import subprocess
from playwright.sync_api import sync_playwright

# --- 1. SETUP FOR DEPLOYMENT & WINDOWS ---
# This ensures it runs on both your local Windows machine AND Streamlit Cloud
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
else:
    # Auto-install browsers if running on Linux (Streamlit Cloud)
    # This prevents the "Executable not found" error on the cloud
    if os.system("playwright install chromium") != 0:
        subprocess.run(["playwright", "install", "chromium"])

# --- CONFIG ---
STAGING_DIR = "Digital Menus"

# --- UTILITY FUNCTIONS ---
def clean_filename(text):
    if not text: return "unknown"
    return re.sub(r'[\\/*?:"<>|]', "", str(text)).strip()

def download_image(img_url, save_path):
    if not img_url: return
    try:
        response = requests.get(img_url, timeout=10)
        if response.status_code == 200:
            with open(save_path, 'wb') as f:
                f.write(response.content)
    except Exception:
        pass

def zip_folder(folder_path, zip_name):
    shutil.make_archive(zip_name, 'zip', folder_path)
    return f"{zip_name}.zip"

def save_individual_csv(data_list, folder_path, restaurant_name, platform):
    if not data_list: return

    df = pd.DataFrame(data_list)  
    export_df = pd.DataFrame()
    export_df['Type'] = ["Product"] * len(df)
    export_df['Category'] = df['Category']
    export_df['Name (EN)'] = df['Dish']
    export_df['Name (AR)'] = ""  
    export_df['Description (EN)'] = df['Description']
    export_df['Description (AR)'] = ""  
    export_df['Price'] = df['Price']
    export_df['Currency'] = df['Currency']
    export_df['Status'] = ["Enabled"] * len(df)
    export_df['Image URL'] = df['Image URL']

    safe_name = clean_filename(restaurant_name)
    # Filename format: RestaurantName_Platform.csv
    filename = f"{safe_name}_{platform}.csv"
    save_path = os.path.join(folder_path, filename)

    export_df.to_csv(save_path, index=False, encoding='utf-8-sig')
    return filename

# --- SCRAPER: ODDMENU ---
def run_scrape_oddmenu(url, progress_callback, image_root):
    data = []
    with sync_playwright() as p:
        # UPDATED: Removed hardcoded Windows path for Cloud compatibility
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        )
        page = context.new_page()

        try:
            progress_callback(f"Accessing {url}...")
            page.goto(url, timeout=60000)
            try:
                page.wait_for_selector('.menu-list__item', timeout=15000)
            except:
                progress_callback(f"Could not load menu items for {url}")
                return data

            try:
                restaurant_name = page.title().split('|')[0].strip()
            except:
                restaurant_name = url.split('/')[-1]
            
            clean_rest_name = clean_filename(restaurant_name)
            
            tabs = page.locator('.menu-list__item .menu__button')
            tab_count = tabs.count()
            
            for t_idx in range(tab_count):
                tabs = page.locator('.menu-list__item .menu__button')
                current_tab = tabs.nth(t_idx)
                tab_name = current_tab.inner_text().strip()
                
                progress_callback(f"--> Processing Tab: {tab_name}")
                current_tab.click()
                time.sleep(2)
                
                category_links = []
                cat_items = page.locator('.category-item')
                for i in range(cat_items.count()):
                    link = cat_items.nth(i).locator('a')
                    title = cat_items.nth(i).locator('h2')
                    if link.count() > 0:
                        href = link.get_attribute('href')
                        c_name = title.inner_text().strip() if title.count() > 0 else "Unknown"
                        if href and href.startswith('/'): href = "https://oddmenu.com" + href
                        category_links.append({'name': c_name, 'href': href})
                
                for cat in category_links:
                    cat_name = cat['name']
                    
                    page.goto(cat['href'], timeout=60000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=5000)
                        page.wait_for_selector('.menu-item', state="attached", timeout=5000)
                    except:
                        continue 

                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(1)
                    page.evaluate("window.scrollTo(0, 0)")

                    dishes = page.locator('.menu-item')
                    item_count = dishes.count()
                    
                    progress_callback(f"----> Category: {cat_name} // {item_count} Items")
                    
                    for d_idx in range(item_count):
                        dish = dishes.nth(d_idx)
                        
                        d_name = dish.locator('.menu-item-title span').first.inner_text().strip()
                        
                        d_desc = ""
                        desc_loc = dish.locator('.menu-item-description p')
                        if desc_loc.count() > 0:
                            d_desc = " ".join(desc_loc.all_inner_texts()).strip()
                        
                        d_price = "0"
                        if dish.locator('.menu-item-price__current b').count() > 0:
                            d_price = dish.locator('.menu-item-price__current b').first.inner_text().strip()
                            
                        d_currency = ""
                        curr_loc = dish.locator('.menu-item-price__current span')
                        if curr_loc.count() > 0:
                            d_currency = curr_loc.first.inner_text().replace(d_price, "").strip()

                        d_img_link = ""
                        img_loc = dish.locator('.menu-item-image__preview-image-link img').first
                        if img_loc.count() > 0:
                            d_img_link = img_loc.get_attribute('src')
                            if not d_img_link: d_img_link = img_loc.get_attribute('data-url')

                        data.append({
                            'Restaurant': restaurant_name,
                            'Tab': tab_name,
                            'Category': cat_name,
                            'Dish': d_name,
                            'Description': d_desc,
                            'Price': d_price,
                            'Currency': d_currency,
                            'Image URL': d_img_link,
                            'Source': url,
                            'Platform': 'OddMenu'
                        })

                        if d_img_link and image_root:
                            folder = os.path.join(image_root, clean_rest_name, clean_filename(cat_name))
                            os.makedirs(folder, exist_ok=True)
                            ext = ".png" if ".png" in d_img_link else ".jpg"
                            fname = f"{clean_filename(d_name)}_{clean_filename(d_price)}{ext}"
                            download_image(d_img_link, os.path.join(folder, fname))
                    
                    page.goto(url)
                    try:
                        page.locator('.menu-list__item').first.wait_for()
                    except:
                        pass

        except Exception as e:
            progress_callback(f"Error scraping {url}: {e}")
        
        browser.close()
    return data


# --- SCRAPER: FINEDINE ---
def run_scrape_finedine(url, progress_callback, image_root):
    data = []
    
    with sync_playwright() as p:
        # UPDATED: Removed hardcoded Windows path for Cloud compatibility
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        try:
            progress_callback(f"Accessing FineDine: {url}")
            page.goto(url, timeout=60000)

            try:
                page.wait_for_selector("button[id^='food-card-link-']", timeout=15000)
            except:
                progress_callback("Could not find menu items.")
                return data

            for _ in range(5): 
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(1)

            restaurant_name = None
            try:
                title_locator = page.locator("span.text-3xl.font-bold.text-primary")
                if title_locator.count() > 0:
                    restaurant_name = title_locator.first.inner_text().strip()
            except:
                pass

            if not restaurant_name:
                page_title = page.title()
                if "|" in page_title: restaurant_name = page_title.split("|")[0].strip()
                elif "-" in page_title: restaurant_name = page_title.split("-")[0].strip()
                else: restaurant_name = page_title

            restaurant_name = restaurant_name.replace("Menu", "").strip()
            if not restaurant_name: restaurant_name = "Unknown_FineDine"
            
            clean_rest_name = clean_filename(restaurant_name)
            progress_callback(f"Detected Name: {restaurant_name}")

            elements_data = page.evaluate("""() => {
                let results = [];
                document.querySelectorAll('span.text-xl.font-bold.text-center.text-primary').forEach(el => {
                    results.push({
                        type: 'header',
                        text: el.innerText,
                        y: el.getBoundingClientRect().top + window.scrollY
                    });
                });
                
                document.querySelectorAll('button[id^="food-card-link-"]').forEach(el => {
                    let name = el.querySelector('span.text-primary.text-base.font-bold')?.innerText || 'Unknown';
                    let priceFull = el.querySelector('span.text-highlight_color')?.innerText || 'NA';
                    let desc = el.querySelector('span.text-primary.text-base.font-normal.line-clamp-2')?.innerText || '';
                    let imgEl = el.querySelector('img');
                    let finalImg = 'No Image';
                    if (imgEl) {
                        if (imgEl.srcset) {
                            let parts = imgEl.srcset.split(',');
                            let bestPart = parts[parts.length - 1].trim(); 
                            finalImg = bestPart.split(' ')[0]; 
                        } else {
                            finalImg = imgEl.src;
                        }
                    }
                    results.push({
                        type: 'item',
                        name: name,
                        price_full: priceFull,
                        description: desc,
                        img: finalImg,
                        y: el.getBoundingClientRect().top + window.scrollY
                    });
                });
                return results.sort((a, b) => a.y - b.y);
            }""")

            
            grouped_data = [] 
            current_cat_name = "Uncategorized"
            current_items = []

            for el in elements_data:
                if el['type'] == 'header':
                    if current_items:
                        grouped_data.append((current_cat_name, current_items))
                    current_cat_name = clean_filename(el['text'])
                    current_items = []
                elif el['type'] == 'item':
                    current_items.append(el)
            
            if current_items:
                grouped_data.append((current_cat_name, current_items))

            for cat_name, items in grouped_data:
                progress_callback(f"--> Category: {cat_name} // {len(items)} Items")
                
                for el in items:
                    dish_name = clean_filename(el['name'])
                    desc_text = el['description'].strip()
                    raw_price = el['price_full'].strip()
                    image_url = el['img']

                    currency = "NA"
                    price_val = "0"
                    
                    if raw_price != "NA":
                        match = re.search(r'[\d\.]+', raw_price)
                        if match:
                            price_val = match.group()
                            currency = raw_price.replace(price_val, "").strip()
                        else:
                            price_val = raw_price
                    
                    if not currency: currency = "NA"

                    if image_url and "http" in image_url:
                        image_url = image_url.replace("filters:blur(125)/", "").replace("filters:blur(125)", "")

                    data.append({
                        'Restaurant': restaurant_name,
                        'Tab': "Menu",
                        'Category': cat_name,
                        'Dish': dish_name,
                        'Description': desc_text,
                        'Price': price_val,
                        'Currency': currency,
                        'Image URL': image_url,
                        'Source': url,
                        'Platform': 'FineDine'
                    })

                    if image_url and "http" in image_url and image_root:
                        folder = os.path.join(image_root, clean_rest_name, cat_name)
                        os.makedirs(folder, exist_ok=True)
                        ext = ".jpg"
                        if ".png" in image_url: ext = ".png"
                        price_clean = clean_filename(price_val)
                        filename = f"{dish_name}_{price_clean}{ext}"
                        full_path = os.path.join(folder, filename)
                        
                        if not os.path.exists(full_path):
                            download_image(image_url, full_path)

        except Exception as e:
            progress_callback(f"Error scraping {url}: {e}")

        browser.close()
    
    return data

# MAIN STREAMLIT APP
def main():
    st.set_page_config(page_title="Menu Scraper", page_icon="🍽️", layout="wide")

    # Cleanup function
    def cleanup_temp():
        if os.path.exists(STAGING_DIR):
            try:
                shutil.rmtree(STAGING_DIR)
            except:
                pass
        if os.path.exists("Digital Menus.zip"):
            os.remove("Digital Menus.zip")

    st.markdown("""
    <style>
        .block-container {padding-top: 2rem;}
        div[data-testid="metric-container"] {
            background-color: #f0f2f6;
            padding: 15px;
            border-radius: 10px;
            border: 1px solid #e0e0e0;
        }
    </style>
    """, unsafe_allow_html=True)

    st.title("🍽️ Digital Menu Scraper")
    st.subheader("Scrape menus from your favorite restaurant platforms!")

    with st.sidebar:
        st.header("⚙️ Configuration")
        
        # Option 1: File Upload
        uploaded_file = st.file_uploader("📂 Option 1: Upload CSV/Excel", type=['csv', 'xlsx'])
        
        st.markdown("**OR**")
        
        # Option 2: Paste Links
        paste_area = st.text_area("🔗 Option 2: Paste Links (One per line)", height=150, help="Paste direct URLs to FineDine or OddMenu here.")
        
        st.divider()
        st.subheader("Filters")
        platform_filter = st.selectbox("Select Platform", ["All Platforms", "FineDine Only", "OddMenu Only"])
        
        st.divider()
        start_btn = st.button("🚀 Start Scraping", type="primary", use_container_width=True)

    # --- PROCESS INPUT (FILE OR TEXT) ---
    df = None
    
    # Priority: File > Paste
    if uploaded_file:
        try:
            if uploaded_file.name.endswith('.csv'):
                df = pd.read_csv(uploaded_file, encoding="cp1252")
            else:
                df = pd.read_excel(uploaded_file)
            df.columns = [c.lower() for c in df.columns]
            if 'url' not in df.columns:
                st.error("❌ Column 'url' missing in file.")
                df = None
        except Exception as e:
            st.error(f"Error reading file: {e}")
            df = None
            
    elif paste_area:
        # Convert text area to DataFrame
        urls = [u.strip() for u in paste_area.split('\n') if u.strip()]
        if urls:
            df = pd.DataFrame({'url': urls})
        else:
            st.warning("Please paste at least one valid URL.")

    # --- MAIN EXECUTION ---
    if start_btn:
        if df is None or df.empty:
            st.error("⚠️ Please upload a file OR paste links to proceed.")
        else:
            # 1. CLEANUP AND SETUP STAGING
            cleanup_temp()
            os.makedirs(STAGING_DIR)

            progress_bar = st.progress(0)
            all_results = []
            
            total_urls = len(df)
            finedine_count = df['url'].apply(lambda x: 1 if "finedine" in str(x).lower() else 0).sum()
            oddmenu_count = df['url'].apply(lambda x: 1 if "oddmenu" in str(x).lower() else 0).sum()

            col1, col2, col3 = st.columns(3)
            col1.metric("Total Restaurants", total_urls)
            col2.metric("FineDine Links", finedine_count)
            col3.metric("OddMenu Links", oddmenu_count)
            
            for index, row in df.iterrows():
                url = str(row['url']).strip()
                is_finedine = "finedine" in url.lower()
                is_oddmenu = "oddmenu" in url.lower()
                
                if platform_filter == "FineDine Only" and not is_finedine: continue
                if platform_filter == "OddMenu Only" and not is_oddmenu: continue

                st.subheader(f"🥣 Processing Restaurant {index + 1}/{total_urls}")
                
                col_status, col_table = st.columns([1, 2])
                
                with col_status:
                    with st.status(f"Scanning {url}...", expanded=True) as status:
                        live_table_ph = col_table.empty()
                        restaurant_data = []
                        
                        platform_name = "Unknown"
                        if is_finedine:
                            platform_name = "FineDine"
                            restaurant_data = run_scrape_finedine(url, status.write, STAGING_DIR)
                        elif is_oddmenu:
                            platform_name = "OddMenu"
                            restaurant_data = run_scrape_oddmenu(url, status.write, STAGING_DIR)
                        else:
                            status.warning("Unknown Platform URL")
                        
                        all_results.extend(restaurant_data)
                        
                        if restaurant_data:
                            live_table_ph.dataframe(pd.DataFrame(restaurant_data))
                            
                            # --- GENERATE INDIVIDUAL CSV ---
                            r_name = restaurant_data[0]['Restaurant']
                            
                            # Save to STAGING_DIR (Using correct naming convention)
                            saved_filename = save_individual_csv(restaurant_data, STAGING_DIR, r_name, platform_name)
                            
                            status.write(f"💾 CSV Created: {saved_filename}")
                            status.update(label="✅ Scraping Complete!", state="complete", expanded=False)
                        else:
                            status.update(label="❌ Failed or Empty", state="error", expanded=False)

                progress_bar.progress((index + 1) / total_urls)
                st.divider()

            st.success("🎉 Batch Processing Finished!")
            
            if all_results:
                final_df = pd.DataFrame(all_results)
                
                # 2. SAVE MASTER CSV
                csv_path = os.path.join(STAGING_DIR, "All_menus_in_one.csv")
                final_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
                
                st.markdown("### 📊 Final Summary")
                st.dataframe(final_df)
                
                # 3. ZIP EVERYTHING
                zip_file = zip_folder(STAGING_DIR, "Digital Menus")
                
                with open(zip_file, "rb") as fp:
                    st.download_button(
                        label="📥 Download All Data (Zip)",
                        data=fp,
                        file_name="Digital Menus.zip",
                        mime="application/zip",
                        type="primary"
                    )
                st.info("Note: Temporary files will be cleaned up automatically on the next run.")
            else:
                st.warning("No data was extracted from any URL.")

if __name__ == "__main__":
    main()