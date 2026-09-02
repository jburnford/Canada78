# External reference datasets (downloaded 2026-08-30)

- `first_nations_location_2026-08-30.csv` — ISC "First Nations Location" open data
  (BAND_NUMBER, BAND_NAME, LONGITUDE, LATITUDE; 638 bands; updated daily).
  Source: https://open.canada.ca/data/en/dataset/b6567c5c-8339-4055-99fa-63f92114d9e4
  CSV: https://data.sac-isc.gc.ca/geomatics/rest/directories/arcgisoutput/DonneesOuvertes_OpenData/Premiere_Nation_First_Nation/Premiere_Nation_First_Nation_CSV.zip
- `../nrcan_aboriginal_lands/` — NRCan "Aboriginal Lands of Canada Legislative Boundaries"
  (GeoBase AL/TA). Per-reserve polygons; attributes NAME1/NAME2 (e.g. "STAR BLANKET I.R. 83D"),
  ALCODE (CLSS code, links to https://clss.nrcan-rncan.gc.ca/mb-nc/en/index.html?can=<ALCODE>),
  ALTYPE (Indian Reserve / Indian Lands / Land Claim Settlement Lands), JUR1. No band number.
  Only the SK .dbf is kept here as a sample; full download:
  https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/geobase_al_ta/shp_eng/AL_TA_CA_SHP_eng.zip (55 MB)
  Catalogue: https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/geobase_al_ta/doc/GeoBase_al_en_Catalogue.pdf
- `../schools/scheduleK_federal_indian_day_schools.{pdf,txt}` — Schedule K of the Federal Indian
  Day Schools settlement (699 schools: province, name, name variants, opening/closing dates,
  location, religious affiliation). Source: https://www.classaction.deloitte.ca/Schedule%20K%20-%20List%20of%20Federal%20Indian%20Day%20Schools.pdf
