from pyspark.sql.functions import col, trim, upper, current_timestamp, lit, when, row_number, to_date
from pyspark.sql.window import Window

# basic parameters
CATALOG = "data2026acc123456"
SCHEMA = "team3"
MAX_POWER_KW = 5000

BRONZE_TABLE = f"{CATALOG}.{SCHEMA}.bronze_equipment"
SILVER_TABLE = f"{CATALOG}.{SCHEMA}.silver_equipment"
QUARANTINE_TABLE = f"{CATALOG}.{SCHEMA}.quarantine_equipment"

df_bronze = spark.read.table(BRONZE_TABLE)

# data cleaning
df_cleaned = (df_bronze
    .withColumn("equipment_id", trim(col("equipment_id")))
    .withColumn("equipment_name", trim(col("equipment_name")))
    .withColumn("equipment_type", trim(col("equipment_type")))
    .withColumn("country", upper(trim(col("country"))))
    .withColumn("site_code", upper(trim(col("site_code"))))
    .withColumn("criticality", upper(trim(col("criticality"))))
)

# dublicate handling
window_spec = Window.partitionBy("equipment_id").orderBy(col("ingestion_timestamp").desc())

df_deduped = (df_cleaned
    .withColumn("rn", row_number().over(window_spec))
    .filter(col("rn") == 1)
    .drop("rn")
)

# date comparing
is_valid_condition = (
    col("equipment_id").isNotNull() & (col("equipment_id") != "") &  
    (col("decommission_date").isNull() | (to_date(col("decommission_date")) >= to_date(col("install_date")))) & 
    (col("rated_power_kw").isNull() | ((col("rated_power_kw") >= 0) & (col("rated_power_kw") <= MAX_POWER_KW))) & 
    (col("rated_vibration_max_mm_s").isNull() | (col("rated_vibration_max_mm_s") >= 0)) 
)

df_evaluated = df_deduped.withColumn(
    "is_valid", is_valid_condition
).withColumn(
    "quarantine_reason",
    when(col("equipment_id").isNull() | (col("equipment_id") == ""), lit("Missing equipment_id"))
    .when(to_date(col("decommission_date")) < to_date(col("install_date")), lit("Decommission date before install date"))
    .when((col("rated_power_kw") < 0) | (col("rated_power_kw") > MAX_POWER_KW), lit("Abnormal Rated Power (out of bounds)"))
    .when(col("rated_vibration_max_mm_s") < 0, lit("Negative Rated Vibration Limit"))
    .otherwise(lit(None))
)

# seperations
df_valid = df_evaluated.filter(col("is_valid") == True).drop("is_valid", "quarantine_reason")

df_quarantine = (df_evaluated
    .filter(col("is_valid") == False)
    .withColumn("quarantine_rule", lit("Equipment EDA Quality Checks"))
    .withColumn("quarantine_timestamp", current_timestamp())
    .drop("is_valid")
)

# write to silver and quarantine tables
df_valid.write.format("delta").mode("overwrite").option("mergeSchema", "true").saveAsTable(SILVER_TABLE)
valid_count = df_valid.count()
print(f"{valid_count} valid records in table: {SILVER_TABLE}")

df_quarantine.write.format("delta").mode("overwrite").option("mergeSchema", "true").saveAsTable(QUARANTINE_TABLE)
quarantine_count = df_quarantine.count()
print(f"{quarantine_count} invalid records in table: {QUARANTINE_TABLE}")

print("Silver process is done!")
