import streamlit as st
from streamlit_folium import st_folium
import pandas as pd
from pathlib import Path
from folium.plugins import MarkerCluster

# --- Page Configuration (MUST be at the very top, before any st. calls) ---
st.set_page_config(
    page_title="BH Network Adequacy Dashboard",
    layout="wide"
)

# --- Password Protection Logic ---
# Initialize session state for authentication
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False

# Load password from secrets.toml (for local development) or Streamlit Cloud secrets
try:
    PASSWORD = st.secrets["app_password"]
except KeyError:
    st.error("Error: 'app_password' not found in secrets.toml. "
             "Please ensure you have a .streamlit/secrets.toml file with 'app_password' defined locally, "
             "or set it in your Streamlit Community Cloud app's secrets.")
    st.stop() # Stop the app if the password secret isn't configured

if not st.session_state["authenticated"]:
    st.title("Login to BH Network Adequacy Dashboard")
    password_input = st.text_input("Enter password:", type="password") # 'type="password"' hides the input
    
    if st.button("Login"):
        if password_input == PASSWORD:
            st.session_state["authenticated"] = True
            st.rerun() # Rerun the app to clear the login screen and show the dashboard
        else:
            st.error("Incorrect password.")
    st.stop() # Stop further execution of the app until authenticated

# --- End Password Protection Logic ---

# --- All code below this line will only execute if the user is authenticated ---

# --- Define File Paths ---
PROJECT_ROOT = Path('.').resolve()
FACILITY_LOCATION_FILES_DIR = PROJECT_ROOT / 'data' /'facility_location_files'
MAPS_DIR = PROJECT_ROOT / 'data' /'maps'
DATA_DIR = PROJECT_ROOT / 'data' / 'map_tables'

st.title("BCBS Behavioral Health Network Adequacy")
st.markdown("National analysis of facility density by raw count and per 100,000 people.")

# --- UI Controls ---
st.sidebar.header("Map Controls")

# Geographic level selection
geographic_level = st.sidebar.radio(
    'Select Geographic Level:',
    ('Zip Code', 'State'),
    help="Choose whether to view data aggregated by zip code or state"
)

# Facility type selection
taxonomy_selection = st.sidebar.radio(
    'Select Facility Taxonomy:',
    (
        '324500000X - Substance Abuse Rehabs', 
        '261QR0405X - SUD Rehab Clinics', 
        '261QM1300X - Multi-Specialty Clinics',
        'Combined - All Healthcare Facilities'
    )
)

# Metric selection
metric_selection = st.sidebar.radio(
    'Select Metric:',
    ('Raw Count', 'Per Capita (per 100k)')
)

# NEW: Facility Overlay Toggle
st.sidebar.markdown("---")
st.sidebar.header("Facility Overlay")
show_facility_overlay = st.sidebar.checkbox("Show Individual Facilities", value=False)

# --- Function to load facility data ---
@st.cache_data
def load_facility_data():
    """Load and combine all facility CSV files"""
    if not FACILITY_LOCATION_FILES_DIR.exists():
        st.warning(f"Facility data directory not found: {FACILITY_LOCATION_FILES_DIR}")
        return None
    
    consolidated_facilities = []
    csv_files = list(FACILITY_LOCATION_FILES_DIR.glob("*.csv"))
    
    if not csv_files:
        st.info(f"No CSV files found in {FACILITY_LOCATION_FILES_DIR}.")
        return None
    
    # Required columns
    required_columns = ['NPI', 'Group Name', 'Street Address', 'City', 'State', 'Zip', 'Latitude', 'Longitude', 'is_substance_abuse_rehab', 'is_sud_rehab_clinic']
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, csv_file in enumerate(csv_files):
        status_text.text(f"Processing facility file {i+1}/{len(csv_files)}: {csv_file.name}")
        try:
            df = pd.read_csv(csv_file)
            
            # Check if required columns exist
            missing_cols = [col for col in required_columns if col not in df.columns]
            if missing_cols:
                st.warning(f"File {csv_file.name} missing columns: {missing_cols}. Skipping this file.")
                continue
            
            # Select only required columns and add source info
            facility_df = df[required_columns].copy()
            facility_df['source_file'] = csv_file.name
            
            # Clean data
            facility_df = facility_df.dropna(subset=['Group Name', 'Latitude', 'Longitude'])
            facility_df = facility_df[
                (facility_df['Latitude'].between(-90, 90)) & 
                (facility_df['Longitude'].between(-180, 180))
            ]
            
            # Clean text fields
            for col in ['Group Name', 'Street Address', 'City', 'State']:
                if col in facility_df.columns:
                    facility_df[col] = facility_df[col].astype(str).str.strip()
            
            # Clean zip codes
            if 'Zip' in facility_df.columns:
                facility_df['Zip'] = facility_df['Zip'].astype(str).str.strip()
                # Convert numeric-like zips (e.g., '12345.0') to '12345'
                facility_df['Zip'] = facility_df['Zip'].apply(lambda x: str(int(float(x))) if pd.notnull(x) and str(x).endswith('.0') else str(x))
                
            boolean_columns = ['is_substance_abuse_rehab', 'is_sud_rehab_clinic']
            for col in boolean_columns:
                if col in facility_df.columns:
                    # Convert 1.0/1 -> 'True', 0.0/0 -> 'False', others/NaN -> 'N/A'
                    facility_df[col] = facility_df[col].apply(lambda x: 
                        'True' if x is True or x == 1.0 or x == 1 
                        else 'False' if x is False or x == 0.0 or x == 0
                        else 'N/A'
                    )
            
            consolidated_facilities.append(facility_df)
            
        except Exception as e:
            st.warning(f"Could not process {csv_file.name}. Error: {e}")
        
        progress_bar.progress((i + 1) / len(csv_files))
    
    progress_bar.empty()
    status_text.empty()
    
    if consolidated_facilities:
        combined_df = pd.concat(consolidated_facilities, ignore_index=True)
        return combined_df
    else:
        return None

def inject_facility_markers_into_html(original_html, facilities_df):
    """Inject facility markers into existing folium HTML using color coding based on taxonomy flags"""
    if facilities_df is None or facilities_df.empty:
        return original_html
    
    # Generate JavaScript code to add facility markers
    facility_js_code = []
    
    # Add MarkerCluster plugin if not already present
    # Check for MarkerCluster.css as a proxy for whether it's already loaded
    if 'markercluster.css' not in original_html.lower():
        facility_js_code.append("""
        // Add MarkerCluster CSS and JS
        var clusterCSS = document.createElement('link');
        clusterCSS.rel = 'stylesheet';
        clusterCSS.href = 'https://unpkg.com/leaflet.markercluster@1.4.1/dist/MarkerCluster.css';
        document.head.appendChild(clusterCSS);
        
        var clusterDefaultCSS = document.createElement('link');
        clusterDefaultCSS.rel = 'stylesheet';
        clusterDefaultCSS.href = 'https://unpkg.com/leaflet.markercluster@1.4.1/dist/MarkerCluster.Default.css';
        document.head.appendChild(clusterDefaultCSS);
        
        var clusterJS = document.createElement('script');
        clusterJS.src = 'https://unpkg.com/leaflet.markercluster@1.4.1/dist/leaflet.markercluster.js';
        document.head.appendChild(clusterJS);
        """)
    
    # Wait for map to be ready and add facility markers
    facility_js_code.append("""
        // Wait for the map to be fully loaded
        setTimeout(function() {
            // Find the map object (folium creates global map variables)
            var mapObj = window[Object.keys(window).find(key => key.startsWith('map_'))];
            
            if (mapObj) {
                // Create marker cluster group
                var facilityCluster = L.markerClusterGroup({
                    showCoverageOnHover: false,
                    zoomToBoundsOnClick: true,
                    spiderfyOnMaxZoom: false,
                    removeOutsideVisibleBounds: true,
                    disableClusteringAtZoom: 13
                });
                
                // Add facility markers
    """)
    
    # Generate markers for each facility
    for idx, facility in facilities_df.iterrows():
        # Determine marker color based on taxonomy flags (ensure these are strings 'True'/'False')
        is_substance_abuse = facility.get('is_substance_abuse_rehab', 'N/A')
        is_sud_clinic = facility.get('is_sud_rehab_clinic', 'N/A')
        
        # Convert string representations to boolean for logic
        is_substance_abuse_bool = (is_substance_abuse == 'True')
        is_sud_clinic_bool = (is_sud_clinic == 'True')
        
        # Determine fill color and facility type description
        if is_substance_abuse_bool and is_sud_clinic_bool:
            fill_color = '#800080'  # Purple - both types
            facility_type_desc = "Substance Abuse Rehab & SUD Rehab Clinic"
        elif is_substance_abuse_bool:
            fill_color = '#FF0000'  # Red - substance abuse rehab only
            facility_type_desc = "Substance Abuse Rehab"
        elif is_sud_clinic_bool:
            fill_color = '#0000FF'  # Blue - SUD rehab clinic only
            facility_type_desc = "SUD Rehab Clinic"
        else:
            fill_color = '#808080'  # Gray - neither type (fallback)
            facility_type_desc = "Other/Unknown"
        
        # Border color for markers (fixed to black for contrast)
        marker_color = '#000000' 

        # Ensure all values are string for popup HTML, handling potential NaNs gracefully
        npi = str(facility.get('NPI', 'N/A'))
        group_name = str(facility.get('Group Name', 'N/A'))
        street_address = str(facility.get('Street Address', 'N/A'))
        city = str(facility.get('City', 'N/A'))
        state = str(facility.get('State', 'N/A'))
        zip_code = str(facility.get('Zip', 'N/A'))
        latitude = f"{facility.get('Latitude', 'N/A'):.4f}" if pd.notnull(facility.get('Latitude')) else 'N/A'
        longitude = f"{facility.get('Longitude', 'N/A'):.4f}" if pd.notnull(facility.get('Longitude')) else 'N/A'
        
        popup_html = f"""
        <b>NPI:</b> {npi}<br>
        <b>Facility Name:</b> {group_name}<br>
        <b>Facility Type:</b> {facility_type_desc}<br>
        <b>Street Address:</b> {street_address}<br>
        <b>City:</b> {city}<br>
        <b>State:</b> {state}<br>
        <b>Zip:</b> {zip_code}<br>
        <b>Coordinates:</b> {latitude}, {longitude}<br>
        <br>
        <b>is_substance_abuse_rehab:</b> {is_substance_abuse}<br>
        <b>is_sud_rehab_clinic:</b> {is_sud_clinic}<br>
        """.replace('"', '\\"').replace('\n', '\\n')
        
        facility_js_code.append(f"""
                var marker_{idx} = L.circleMarker([{facility['Latitude']}, {facility['Longitude']}], {{
                    radius: 7,
                    color: '{marker_color}',
                    weight: 2,
                    opacity: 0.9,
                    fillColor: '{fill_color}',
                    fillOpacity: 0.7
                }}).bindPopup("{popup_html}", {{maxWidth: 300}});
                
                facilityCluster.addLayer(marker_{idx});
        """)
    
    # Close the JavaScript function and add legend
    facility_js_code.append("""
                // Add cluster to map
                mapObj.addLayer(facilityCluster);
                
                // Add layer control if it doesn't exist
                if (!mapObj.layerControl) {
                    var baseLayers = {};
                    var overlayLayers = {
                        "Individual Facilities": facilityCluster
                    };
                    mapObj.layerControl = L.control.layers(baseLayers, overlayLayers).addTo(mapObj);
                } else {
                    // Check if "Individual Facilities" overlay already exists to avoid duplication on rerun
                    var existingOverlays = mapObj.layerControl._layers.filter(layer => layer.overlay).map(layer => layer.name);
                    if (!existingOverlays.includes("Individual Facilities")) {
                        mapObj.layerControl.addOverlay(facilityCluster, "Individual Facilities");
                    }
                }
            }
        }, 1000);
    """)
    
    # Combine all JavaScript code
    full_js_code = '\n'.join(facility_js_code)
    
    # Inject the JavaScript into the HTML
    script_tag = f"<script>{full_js_code}</script>"
    
    # Find the closing </body> tag and insert before it
    if '</body>' in original_html:
        modified_html = original_html.replace('</body>', f'{script_tag}\n</body>')
    else:
        # If no </body> tag found, append to the end
        modified_html = original_html + script_tag
    
    return modified_html

# --- Dynamic Filename and Path Generation ---
# Extract facility type name from taxonomy selection
if taxonomy_selection.startswith('Combined'):
    facility_type = 'All Healthcare Facilities'
else:
    facility_type = taxonomy_selection.split(' - ')[1]

# Construct the common base name from selections
if metric_selection == 'Raw Count':
    map_name_construct = f"{facility_type} (Raw)"
else:
    map_name_construct = f"{facility_type} (per 100k)"

# Clean up the name to match the saved filename format
base_filename = f"{map_name_construct.replace(' ', '_').replace('(', '').replace(')', '')}"

# Generate the final file paths based on the geographic level
if geographic_level == 'Zip Code':
    map_filename = f"zipcode_map_{base_filename}.html"
    data_filename = f"zipcode_data_{base_filename}.csv"
    data_path = DATA_DIR / 'zip_code_counts' / data_filename
    map_path = MAPS_DIR / map_filename
elif geographic_level == 'State':
    map_filename = f"state_map_{base_filename}.html"
    data_filename = f"state_data_{base_filename}.csv"
    data_path = DATA_DIR / 'state_counts' / data_filename
    map_path = MAPS_DIR / map_filename

display_title = f"{geographic_level}: {map_name_construct}"
st.header(f"Displaying: {display_title}")

# Add context about the selected geographic level and taxonomy
if geographic_level == 'Zip Code':
    st.info("**Zip code-level view**: Shows granular, localized data. Note: Only zip codes with facilities are displayed.")
elif geographic_level == 'State':
    st.info("**State-level view**: Shows aggregated data for all facilities within each state, providing a high-level regional comparison.")

# Add information about the combined taxonomy when selected
if taxonomy_selection.startswith('Combined'):
    st.success("**Combined View**: This map shows the total count of all three facility types combined (Substance Abuse Rehabs + SUD Rehab Clinics + Multi-Specialty Clinics), providing a comprehensive view of behavioral health facility density.")

# Handle facility overlay creation and display
facility_data = None
if show_facility_overlay:
    with st.spinner("Loading individual facility data..."): # Added spinner for UX
        facility_data = load_facility_data()
    if facility_data is None or facility_data.empty:
        st.warning("No individual facility data loaded or found. Overlay will not be shown.")


# Display the selected map
try:
    with open(map_path, 'r', encoding='utf-8') as f:
        map_html = f.read()
    
    # If facility overlay is enabled, inject facility markers into the existing map HTML
    if show_facility_overlay and facility_data is not None and not facility_data.empty:
        map_html = inject_facility_markers_into_html(map_html, facility_data)
    
    st.components.v1.html(map_html, height=1000, scrolling=True)
        
except FileNotFoundError:
    st.error(f"Map file not found: '{map_path.name}'.")
    st.info(f"Please ensure this file exists in the '{MAPS_DIR}' directory.")
    # Debugging help
    if MAPS_DIR.exists():
        available_files = [f.name for f in MAPS_DIR.glob('*.html')]
        if available_files:
            st.write("Available map files:", available_files)
except Exception as e: # Catch any other unexpected errors during map display
    st.error(f"An unexpected error occurred while loading or displaying the map: {e}")

# --- Optional: Display Data Table ---
show_data = st.sidebar.checkbox("Show Raw Data Table")
if show_data:
    st.subheader(f"Underlying Data for: {display_title}")
    
    try:
        # Attempt to load the dynamically selected data file
        df = pd.read_csv(data_path)
        
        if geographic_level == 'Zip Code':
            # Interactive Filters for Zip Code Data
            st.markdown("---")
            st.write("#### Filter Data")

            # Create a copy of the dataframe to apply filters to
            filtered_df = df.copy()

            # Lay out filters in columns for a cleaner look
            col1, col2 = st.columns(2)

            # Filter 1: State Multi-Select
            with col1:
                if 'state' in filtered_df.columns:
                    unique_states = sorted(filtered_df['state'].unique())
                    selected_states = st.multiselect(
                        'Filter by State(s):',
                        options=unique_states,
                        default=[]
                    )
                    if selected_states:
                        filtered_df = filtered_df[filtered_df['state'].isin(selected_states)]
            
            # Filter 2: Metric Value Range Slider
            with col2:
                try:
                    # Dynamically identify the metric column name
                    possible_metric_cols = [
                        col for col in df.columns 
                        if col not in ['zipcode', 'state', 'population'] and not col.endswith('_capped_viz')
                    ]
                    
                    metric_col_name = None
                    if possible_metric_cols:
                        # Try to find a column name that approximately matches the selected metric
                        metric_keyword = map_name_construct.replace(' (Raw)', '').replace(' (per 100k)', '').replace(' ', '_').lower()
                        matching_cols = [col for col in possible_metric_cols if metric_keyword in col.lower()]
                        if matching_cols:
                            metric_col_name = matching_cols[0]
                        else:
                            metric_col_name = possible_metric_cols[0] # Fallback to first available
                    
                    if metric_col_name and metric_col_name in filtered_df.columns:
                        metric_data_for_slider = filtered_df[metric_col_name].dropna()

                        if not metric_data_for_slider.empty:
                            min_val = float(metric_data_for_slider.min())
                            max_val = float(metric_data_for_slider.max())
                            
                            if min_val == max_val:
                                st.info(f"All values for '{map_name_construct}' are {min_val}. No range to filter.")
                                selected_range = (min_val, max_val) # Set range to prevent slider error
                            else:
                                # Adjust format based on metric type
                                slider_format = "%.2f" if "per 100k" in map_name_construct else "%d"
                                selected_range = st.slider(
                                    f"Filter by '{map_name_construct}':",
                                    min_value=min_val,
                                    max_value=max_val,
                                    value=(min_val, max_val),
                                    format=slider_format
                                )
                            
                            filtered_df = filtered_df[
                                filtered_df[metric_col_name].between(selected_range[0], selected_range[1])
                            ]
                        else:
                            st.info("Metric data for slider is empty.")
                    else:
                        st.info("Value filter not available (metric column not found or identified).")
                except (IndexError, KeyError) as e:
                    st.info(f"Value filter not available due to data structure: {e}")
                except ValueError as e:
                    st.warning(f"Could not process metric values for slider. Error: {e}")


            st.markdown("---")

            # Display Summary Metrics and the Filtered DataFrame
            metric_col1, metric_col2 = st.columns(2)
            metric_col1.metric(f"Total Records in Original Table", f"{len(df):,}")
            metric_col2.metric("Records Matching Filters", f"{len(filtered_df):,}")

            display_cols = [col for col in filtered_df.columns if not col.endswith('_capped_viz')]
            st.dataframe(filtered_df[display_cols], use_container_width=True)

        else:
            # For State level, just display the table directly without filters
            st.metric(f"Total {geographic_level} Records in Table", f"{len(df):,}")
            display_cols = [col for col in df.columns if not col.endswith('_capped_viz')]
            st.dataframe(df[display_cols], use_container_width=True)

    except FileNotFoundError:
        st.error(f"Data file not found: '{data_path.name}'.")
        st.info(f"Please ensure this file exists in the correct directory: '{data_path.parent}'")
    except Exception as e:
        st.error(f"An error occurred while loading the data: {e}")

# --- Footer ---
st.sidebar.markdown("---")
st.sidebar.markdown("**Data Notes:**")
st.sidebar.markdown("- Per capita rates exclude areas with <5000 population")
st.sidebar.markdown("- Zip code maps show only areas with available data or shapefiles")
st.sidebar.markdown("- Zip-codes with 98th percentile and above facility count have been visually capped to prevent skewing. True counts can be found in the entity's tooltip")
st.sidebar.markdown("- State maps include all 50 states + DC")
st.sidebar.markdown("- All maps use linear binning to preserve true disparities")
st.sidebar.markdown("- All maps are available individually in provided maps folder")
st.sidebar.markdown("- **Combined maps** show the total of all three facility types for comprehensive analysis")
if show_facility_overlay:
    st.sidebar.markdown("- **Facility Overlay**: Markers show individual facility locations with detailed information")
    # Add explicit legend in sidebar
    st.sidebar.markdown("  - <span style='color:#FF0000; font-size: 16px;'>●</span> Substance Abuse Rehab", unsafe_allow_html=True)
    st.sidebar.markdown("  - <span style='color:#0000FF; font-size: 16px;'>●</span> SUD Rehab Clinic", unsafe_allow_html=True)
    st.sidebar.markdown("  - <span style='color:#800080; font-size: 16px;'>●</span> Both Types", unsafe_allow_html=True)
    st.sidebar.markdown("  - <span style='color:#808080; font-size: 16px;'>●</span> Other/Unknown", unsafe_allow_html=True)