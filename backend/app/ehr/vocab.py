"""Code systems of the synthetic EHR. Each code was checked against the NLM APIs on 5 October 2026:
ICD-10-CM and LOINC with clinicaltables.nlm.nih.gov, RxNorm ingredients with rxnav.nlm.nih.gov."""

# condition key ->. ICD-10-CM code, name, note words.
CONDITIONS: dict[str, tuple[str, str, list[str]]] = {
    "t2dm": ("E11.9", "Type 2 diabetes mellitus without complications", ["type 2 diabetes", "T2DM", "DM2", "diabetes mellitus type 2"]),
    "t2dm_ckd": ("E11.22", "Type 2 diabetes mellitus with diabetic chronic kidney disease", ["diabetic kidney disease"]),
    "t1dm": ("E10.9", "Type 1 diabetes mellitus without complications", ["type 1 diabetes", "T1DM"]),
    "gdm_history": ("Z86.32", "Personal history of gestational diabetes", ["gestational diabetes"]),
    "htn": ("I10", "Essential (primary) hypertension", ["hypertension", "HTN"]),
    "ckd3a": ("N18.31", "Chronic kidney disease, stage 3a", ["CKD stage 3a", "chronic kidney disease"]),
    "ckd3b": ("N18.32", "Chronic kidney disease, stage 3b", ["CKD stage 3b", "chronic kidney disease"]),
    "ckd4": ("N18.4", "Chronic kidney disease, stage 4 (severe)", ["CKD stage 4", "chronic kidney disease"]),
    "esrd": ("N18.6", "End stage renal disease", ["ESRD", "end stage renal disease"]),
    "dialysis": ("Z99.2", "Dependence on renal dialysis", ["hemodialysis", "dialysis"]),
    "hfref": ("I50.22", "Chronic systolic (congestive) heart failure", ["HFrEF", "systolic heart failure", "heart failure with reduced ejection fraction"]),
    "afib": ("I48.91", "Unspecified atrial fibrillation", ["atrial fibrillation", "AF", "afib"]),
    "adrenal_insufficiency": ("E27.1", "Primary adrenocortical insufficiency", ["adrenal insufficiency", "Addison"]),
    "aki": ("N17.9", "Acute kidney failure, unspecified", ["acute kidney injury", "AKI"]),
    "kidney_transplant": ("Z94.0", "Kidney transplant status", ["kidney transplant", "renal transplant"]),
    "heart_transplant": ("Z94.1", "Heart transplant status", ["heart transplant"]),
    "attr_amyloid": ("E85.82", "Wild-type transthyretin-related (ATTR) amyloidosis", ["amyloidosis", "ATTR"]),
    "aortic_stenosis": ("I35.0", "Nonrheumatic aortic (valve) stenosis", ["aortic stenosis"]),
    "obesity": ("E66.9", "Obesity, unspecified", ["obesity"]),
    "cad": ("I25.10", "Atherosclerotic heart disease of native coronary artery without angina pectoris", ["coronary artery disease", "CAD"]),
    "stroke": ("I63.9", "Cerebral infarction, unspecified", ["stroke", "CVA"]),
    "tia": ("G45.9", "Transient cerebral ischemic attack, unspecified", ["TIA", "transient ischemic attack"]),
    "nstemi": ("I21.4", "Non-ST elevation (NSTEMI) myocardial infarction", ["NSTEMI", "myocardial infarction"]),
    "pdr_dme": ("E11.3513", "Type 2 diabetes mellitus with proliferative diabetic retinopathy with macular edema, bilateral",
                ["proliferative diabetic retinopathy", "macular edema"]),
    "mild_npdr": ("E11.3293", "Type 2 diabetes mellitus with mild nonproliferative diabetic retinopathy without macular edema, bilateral",
                  ["nonproliferative diabetic retinopathy", "NPDR"]),
    "pregnancy": ("Z33.1", "Pregnant state, incidental", ["pregnant", "pregnancy"]),
    "hyperlipidemia": ("E78.5", "Hyperlipidemia, unspecified", ["hyperlipidemia"]),
    "copd": ("J44.9", "Chronic obstructive pulmonary disease, unspecified", ["COPD"]),
    "hyperkalemia": ("E87.5", "Hyperkalemia", ["hyperkalemia"]),
    "palliative": ("Z51.5", "Encounter for palliative care", ["palliative care", "hospice"]),
}

# medication key ->. RxNorm ingredient RXCUI, name, class.
MEDICATIONS: dict[str, tuple[str, str, str]] = {
    "metformin": ("6809", "metformin", "biguanide"),
    "empagliflozin": ("1545653", "empagliflozin", "sglt2"),
    "dapagliflozin": ("1488564", "dapagliflozin", "sglt2"),
    "insulin_glargine": ("274783", "insulin glargine", "insulin"),
    "insulin_lispro": ("86009", "insulin lispro", "insulin"),
    "semaglutide": ("1991302", "semaglutide", "glp1"),
    "tirzepatide": ("2601723", "tirzepatide", "glp1"),
    "glipizide": ("4821", "glipizide", "sulfonylurea"),
    "sitagliptin": ("593411", "sitagliptin", "dpp4"),
    "pioglitazone": ("33738", "pioglitazone", "tzd"),
    "lisinopril": ("29046", "lisinopril", "acei"),
    "losartan": ("52175", "losartan", "arb"),
    "valsartan": ("69749", "valsartan", "arb"),
    "sacubitril": ("1656328", "sacubitril", "arni"),
    "furosemide": ("4603", "furosemide", "loop"),
    "bumetanide": ("1808", "bumetanide", "loop"),
    "torsemide": ("38413", "torsemide", "loop"),
    "carvedilol": ("20352", "carvedilol", "beta_blocker"),
    "metoprolol": ("6918", "metoprolol", "beta_blocker"),
    "spironolactone": ("9997", "spironolactone", "mra"),
    "digoxin": ("3407", "digoxin", "digoxin"),
    "atorvastatin": ("83367", "atorvastatin", "statin"),
    "amlodipine": ("17767", "amlodipine", "ccb"),
    "hydrochlorothiazide": ("5487", "hydrochlorothiazide", "thiazide"),
    "apixaban": ("1364430", "apixaban", "anticoagulant"),
    "prednisone": ("8640", "prednisone", "steroid"),
    "tacrolimus": ("42316", "tacrolimus", "immunosuppressant"),
}

# analyte key ->. LOINC, name, canonical unit.
LABS: dict[str, tuple[str, str, str]] = {
    "hba1c": ("4548-4", "Hemoglobin A1c/Hemoglobin.total in Blood", "%"),
    "egfr": ("98979-8", "Glomerular filtration rate by Creatinine-based formula (CKD-EPI 2021)/1.73 sq M", "mL/min/1.73m2"),
    "uacr": ("9318-7", "Albumin/Creatinine [Mass Ratio] in Urine", "mg/g"),
    "potassium": ("2823-3", "Potassium [Moles/volume] in Serum or Plasma", "mmol/L"),
    "sodium": ("2951-2", "Sodium [Moles/volume] in Serum or Plasma", "mmol/L"),
    "ntprobnp": ("33762-6", "Natriuretic peptide.B prohormone N-Terminal [Mass/volume] in Serum or Plasma", "pg/mL"),
    "lvef": ("10230-1", "Left ventricular Ejection fraction", "%"),
    "bmi": ("39156-5", "Body mass index (BMI) [Ratio]", "kg/m2"),
    "sbp": ("8480-6", "Systolic blood pressure", "mm[Hg]"),
    "dbp": ("8462-4", "Diastolic blood pressure", "mm[Hg]"),
}

# Each LOINC code that measures an analyte. The engine reads a lab by its analyte, so a mapped local code and
# a second LOINC code of the same test count the same.
LOINC_ANALYTE: dict[str, str] = {
    "4548-4": "hba1c", "17856-6": "hba1c", "59261-8": "hba1c",
    "98979-8": "egfr", "62238-1": "egfr", "33914-3": "egfr",
    "9318-7": "uacr", "14959-1": "uacr", "30000-4": "uacr",
    "2823-3": "potassium", "2951-2": "sodium",
    "33762-6": "ntprobnp", "30934-4": "bnp", "42637-9": "bnp",
    "10230-1": "lvef", "18043-0": "lvef",
    "39156-5": "bmi", "8480-6": "sbp", "8462-4": "dbp",
}

# Lab codes of the clinics. Eastside's point-of-care and reference lab codes have no LOINC code in its system:
# the terminology agent proposes the mapping, and a person approves it.
LOCAL_LAB_CODES: list[dict] = [
    {"source_code": "NFM-HBA1C", "source_name": "HEMOGLOBIN A1C", "unit": "%", "loinc": "4548-4", "clinic": "NFM"},
    {"source_code": "NFM-EGFR", "source_name": "EGFR (CKD-EPI 2021)", "unit": "mL/min/1.73m2", "loinc": "98979-8", "clinic": "NFM"},
    {"source_code": "NFM-UACR", "source_name": "ALBUMIN/CREATININE RATIO, URINE", "unit": "mg/g", "loinc": "9318-7", "clinic": "NFM"},
    {"source_code": "NFM-K", "source_name": "POTASSIUM, SERUM", "unit": "mmol/L", "loinc": "2823-3", "clinic": "NFM"},
    {"source_code": "NFM-NA", "source_name": "SODIUM, SERUM", "unit": "mmol/L", "loinc": "2951-2", "clinic": "NFM"},
    {"source_code": "ECH-POCA1C", "source_name": "POC HGB A1C (IFCC)", "unit": "mmol/mol", "loinc": None, "clinic": "ECH"},
    {"source_code": "ECH-EGFR21", "source_name": "GFR EST CKDEPI21", "unit": "mL/min/1.73m2", "loinc": None, "clinic": "ECH"},
    {"source_code": "ECH-MALBCR", "source_name": "URINE MICROALB/CREAT RATIO", "unit": "mg/mmol", "loinc": None, "clinic": "ECH"},
    {"source_code": "ECH-K", "source_name": "POTASSIUM", "unit": "mmol/L", "loinc": "2823-3", "clinic": "ECH"},
    {"source_code": "ECH-NA", "source_name": "SODIUM", "unit": "mmol/L", "loinc": "2951-2", "clinic": "ECH"},
    {"source_code": "MHK-NTBNP", "source_name": "NT-PROBNP", "unit": "pg/mL", "loinc": "33762-6", "clinic": "MHK"},
    {"source_code": "MHK-EGFR", "source_name": "EGFR CKD-EPI 2021", "unit": "mL/min/1.73m2", "loinc": "98979-8", "clinic": "MHK"},
    {"source_code": "MHK-UACR", "source_name": "URINE ALBUMIN/CREATININE", "unit": "mg/g", "loinc": "9318-7", "clinic": "MHK"},
    {"source_code": "MHK-K", "source_name": "POTASSIUM, PLASMA", "unit": "mmol/L", "loinc": "2823-3", "clinic": "MHK"},
    {"source_code": "MHK-NA", "source_name": "SODIUM, PLASMA", "unit": "mmol/L", "loinc": "2951-2", "clinic": "MHK"},
    {"source_code": "MHK-HBA1C", "source_name": "HGB A1C", "unit": "%", "loinc": "4548-4", "clinic": "MHK"},
    {"source_code": "VS-BMI", "source_name": "BMI", "unit": "kg/m2", "loinc": "39156-5", "clinic": "ALL"},
    {"source_code": "VS-SBP", "source_name": "BP SYSTOLIC", "unit": "mm[Hg]", "loinc": "8480-6", "clinic": "ALL"},
    {"source_code": "VS-DBP", "source_name": "BP DIASTOLIC", "unit": "mm[Hg]", "loinc": "8462-4", "clinic": "ALL"},
]

CLINICS = {
    "NFM": "Northside Family Medicine",
    "ECH": "Eastside Community Health Center",
    "MHK": "Meridian Heart & Kidney Clinic",
}
