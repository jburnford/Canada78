# 1902 Schedule extraction: sched1902_qwen38_medium vs Sonnet reference
chunks compared: [0, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
reference reserve rows: 1,422 | model reserve rows: 1,444
matched: 1,422 (100.0% of reference) — {'exact': 1317, 'fuzzy': 25, 'relaxed': 3, 'name': 77}
missed (in reference, not found in model): 0
extra (in model, not in reference): 22

## field agreement on matched rows
- reserve_no: exact 100.0%
- acres: exact 98.6%
- location: exact 98.0%, near (Jaccard≥0.9) 99.2%
- tribe_band: exact 98.1%, near (Jaccard≥0.9) 98.1%
- remarks: exact 98.0%, near (Jaccard≥0.9) 98.4%

## confidence distribution
- reference: {'high': 1307, 'medium': 82, 'low': 33}
- model: {'high': 1368, 'medium': 72, 'low': 4}

## missed rows (first 40 of 0)

## extra model rows (first 40 of 22)
- p63 BABINE AGENCY | ... | ... | ...
- p71 COWICHAN AGENCY | ... | ... | ...
- p71 COWICHAN AGENCY | ... | ... | ...
- p71 COWICHAN AGENCY | ... | ... | ...
- p71 COWICHAN AGENCY | ... | ... | ...
- p71 COWICHAN AGENCY | ... | ... | ...
- p71 COWICHAN AGENCY | ... | ... | ...
- p71 COWICHAN AGENCY | ... | ... | ...
- p71 COWICHAN AGENCY | ... | ... | ...
- p71 COWICHAN AGENCY | ... | ... | ...
- p71 COWICHAN AGENCY | ... | ... | ...
- p71 COWICHAN AGENCY | ... | ... | ...
- p71 COWICHAN AGENCY | ... | ... | ...
- p71 COWICHAN AGENCY | ... | ... | ...
- p71 COWICHAN AGENCY | ... | ... | ...
- p71 COWICHAN AGENCY | ... | ... | ...
- p71 COWICHAN AGENCY | ... | ... | ...
- p72 COWICHAN AGENCY | ... | ... | ...
- p72 COWICHAN AGENCY | ... | ... | ...
- p112 KAMLOOPS - OKANAGAN AGENCY | None | None | None
- p183 WILLIAMS LAKE AGENCY | ... | ... | ...
- p189 WILLIAMS LAKE AGENCY | 2 | Garden reserve | 80

## field disagreements (first 60 of 82)
- [remarks] p17 Betsiamits.: ref='A portion of the area set apart lander the Act 14 and 15 Vic., chap. 106. (See No. 1.)' vs model='A portion of the area set apart under the Act 14 and 15 Vic., chap. 106. (See No. 1.)'
- [remarks] p28 Sarnia: ref='Reserved by these Indians in the cession of a large tract in the London and Western distri' vs model='None'
- [acres] p34 Temporary hay lands, S.W. 1/4 20, 20, 21 W.I.M.: ref='.25' vs model='.25'
- [acres] p39 Agency reserve at Macleod: ref='.003' vs model='.003'
- [remarks] p34 Pine River, Lake Winnipegosis. Frac. Tps. 35, 36 and 37, R.19. W.I.M. (not surveyed) have been added to the Pine River reserve.: ref='O.C. Jan. 1, 1902. (File 162,550.)' vs model='Frac. Tps. 35, 36 and 37, R.19. W.I.M. (not surveyed) have been added to the Pine River re'
- [location] p63 Coryatsaqua: ref='Cassiar district, on the trail to Hazelton, about two miles north of Lachkaltsap.' vs model='Cassiar district, on the trail to Hazelton.'
- [remarks] p70 Comox: ref='Allotted by Joint Reserve Commission, December 12, 1876.' vs model='Allotted by Joint Reserve Commission, December 12, 1876. Surveyed 1878.'
- [location] p71 Cowichan: ref='Quamichan district, rge. 2, sec. 12, 13, 14; Quamichan district, rge. 3, sec. 12, 13, 14, ' vs model='Quamichan district, rge. 2, sec. 12, 13, 14.'
- [tribe_band] p71 Cowichan: ref='Cowichan; on one parcel (rge. 4) also listed as held by Quamichan, Comiaken, Clem-clem-a-l' vs model='Cowichan'
- [acres] p72 None: ref='75' vs model='...'
- [tribe_band] p72 None: ref='None' vs model='75'
- [remarks] p73 South Saanich: ref='Original reserves confirmed by Joint Reserve Commission, March 3, 1877. Surveyed, 1878.' vs model='Allotted by Joint Reserve Commission, March 3, 1877. Surveyed, 1878.'
- [location] p73 East Saanich: ref='South Saanich district, sections 6, 7, 8 S., range 4 E., sections 6, 7, 8 S., range 5 E., ' vs model='South Saanich district, sections 6, 7, 8S., range 4E., sections 6, 7, 8S., range 5E., sect'
- [tribe_band] p76 Kwaw-kwaw-a-pilt: ref='Chilliwack, Kwaw-kwaw-a-pilt band' vs model='Kwaw-kwaw-a-pilt band'
- [location] p81 Homalco: ref='Coast district, on right bank of Homalco river, at the head of Bute inlet.' vs model='Coast district, on right bank of Homalco river, at the bead of Bute inlet.'
- [remarks] p87 Semiahmoo: ref="Allotted by Comm'r O'Reilly, June 14, 1887. Surveyed, 1890. Final confirmation, March 26, " vs model="Allotted by Comm'r O'Reilly, June 14, 1887. Surveyed, 1990. Final confirmation, March 26, "
- [tribe_band] p93 Seabird Island: ref='Squawtits' vs model='...'
- [location] p94 Yaalstrick: ref='New Westminster district, in sections 28, 29, 32 and 33, township 23, east of coast meridi' vs model='New Westminster district, in sections 28, 29, 32 and 33, township 23, east of coast meridi'
- [remarks] p99 Squeah: ref='Foreshore added to this reserve by Order in Council, August 9, 1900; 20 inches of water re' vs model="Allotted by Comm'r. O'Reilly, May 23, 1881. 20 inches of water from Yale creek recorded Se"
- [location] p99 Kaykaip: ref='Yale district, in township 6, range 26, west of 6th meridian, on left bank of Fraser river' vs model='Yale district, in township 6, range 26, west of 6th meridian, on left bank of Fraser river'
- [remarks] p99 Kaykaip: ref="Allotted by Comm'r O'Reilly, May 23, 1881. 20 inches of water from Kaykaip creek recorded " vs model='...'
- [remarks] p99 Stullawheets: ref="Allotted by Comm'r. Sproat, August 5, 1879. 50 inches of water from Stullawheets creek rec" vs model='...'
- [remarks] p99 Lirkseetsis-sum: ref="In railway belt. Allotted by Comm'r. Sproat, August 5, 1879. Surveyed 1882 - 84. Final con" vs model="In railway belt. Allotted by Comm'r. Sproat, August 5, 1879. Surveyed 1882 - 84. Final con"
- [tribe_band] p102 Hustalen: ref='Adams Lake, Sabhaltkum band' vs model='Adams Lake, Sahhaltkum band'
- [acres] p104 Tsawawmuck: ref='47 1/2' vs model='47 1/2'
- [tribe_band] p104 Tsawawmuck: ref='Boothroyd, Chomok band' vs model='Chomok band'
- [acres] p106 Speyum: ref='374 1/2' vs model='374 1/2'
- [acres] p106 Chukcheetso: ref='44 1/2' vs model='44 1/2'
- [acres] p106 Staiyahanny: ref='74 1/2' vs model='74 1/2'
- [acres] p106 Dufferin reserve: ref='15 1/2' vs model='15 1/2'
- [location] p106 Kopchitchin: ref='Yale district, on the right bank of the Fraser, at North Bend, 2 miles above Boston Bar, w' vs model='Yale district, on the right bank of the Fraser, at North Bend, 2 miles above Boston Bar, t'
- [acres] p107 Austin's Flat: ref='3 1/4' vs model='3 1/4'
- [acres] p108 Paul's: ref='1 3/4' vs model='1 3/4'
- [acres] p108 Kumcheen: ref='21 3/4' vs model='21 3/4'
- [acres] p108 Shawniken: ref='106 1/5' vs model='106 1/5'
- [location] p108 None: ref='Kamloops division of Yale district, on the left bank of the Thompson river, to the south o' vs model='Kamloops division of Yale district, on the left bank of the Thompson river, to the south o'
- [remarks] p113 Whyeek: ref='100 inches of water recorded from Whyeek creek, all the water from a neighbouring spring, ' vs model='and all the water from all other sources of water-supply on the reserve. The Kanaka Bar re'
- [remarks] p127 Tsinstikeptum: ref="Allotted by Commissioner O'Reilly, October 19, 1888. Surveyed 1889. Final confirmation, Ap" vs model='1888. Surveyed 1889. Final confirmation, April 28, 1891.'
- [remarks] p128 Osoyoos: ref='50 inches of water recorded from A-tsi-hlak creek; 100 inches from Wolf creek and 300 inch' vs model='50 inches of water recorded from A-tsi-hlak creek; 100 inches from Wolf creek and 300 inch'
- [remarks] p128 Dog Lake: ref='The Osoyoos reserves were allotted by Joint Reserve Commission, Nov. 21, 1877. Surveyed, 1' vs model='Surveyed, 1889. Final confirmation April 28, 1991.'
- [remarks] p129 Meadow: ref='Allotted by Joint Reserve Commission, August 14, 1877. Surveyed, 1878 and 1884. Final conf' vs model='Allotted by Joint Reserve Commission, August 14, 1877. Surveyed, 1978 and 1884. Final conf'
- [location] p131 Ashnola: ref='On the right bank of the Similkaneen river, to the south of and adjoining reserve No. 10.' vs model='On the right bank of the Simikaneen river, to the south of and adjoining reserve No. 10.'
- [tribe_band] p142 Tsawwati: ref='Tanock-teuch and Ahwaheet-tlala bands' vs model='Knights Inlet, Tanock-teuch and Ahwaheet-tlala bands'
- [tribe_band] p142 Keogh: ref='Tanock-teuch and Ahwaheet-tlala bands' vs model='Knights Inlet, Tanock-teuch and Ahwaheet-tlala bands'
- [tribe_band] p142 Kwatse: ref='Tanock-teuch and Ahwaheet-tlala bands' vs model='Knights Inlet, Tanock-teuch and Ahwaheet-tlala bands'
- [tribe_band] p142 Grave-yard: ref='Tanock-teuch and Ahwaheet-tlala bands' vs model='Knights Inlet, Tanock-teuch and Ahwaheet-tlala bands'
- [remarks] p150 Kitimat: ref="Allotted by Comm'r. O'Reilly, July 24, 1889. Surveyed, 1891. Final confirmation, May 4, 18" vs model="Allotted by Comm'r. O'Reilly, July 24, 1889. Surveyed, 1891. Final confirmation, May 4, 19"
- [remarks] p150 Tahla: ref="Allotted by Comm'r. O'Reilly, July 22, 1889. Surveyed, 1891. Final confirmation, May 4, 18" vs model="Allotted by Comm'r. O'Reilly, July 22, 1889. Surveyed, 1891. Final confirmation, May 4, 19"
- [remarks] p154 Wekellals: ref="Allotted by Commissioner O'Reilly, July 25, 1889. Surveyed, 1891. Final confirmation, May " vs model="Allotted by Commissioner O'Reilly, July 25, 1889. Surveyed, 1891. Final confirmation, May "
- [location] p154 Kokyet: ref='Coast district, on Yeo island, at the mouth of Ellerslie channel.' vs model='Coast district, on Yeo island, at'
- [remarks] p154 Kokyet: ref="Allotted by Commissioner O'Reilly, August 29, 1882. Surveyed, 1888. Final confirmation, Ma" vs model="Allotted by Commissioner O'Reilly, August 29, 1882. Surveyed, 1888."
- [tribe_band] p156 Heillen: ref='Massett' vs model='Masset'
- [tribe_band] p156 Yagan: ref='Massett' vs model='Masset'
- [tribe_band] p156 Lanas: ref='Massett' vs model='Masset'
- [tribe_band] p156 Satunquin: ref='Massett' vs model='Masset'
- [tribe_band] p156 Ain: ref='Massett' vs model='Masset'
- [tribe_band] p156 Yan: ref='Massett' vs model='Masset'
- [tribe_band] p156 Meagwan: ref='Massett' vs model='Masset'
- [tribe_band] p156 Kose: ref='Massett' vs model='Masset'
- [tribe_band] p156 Naden: ref='Massett' vs model='Masset'