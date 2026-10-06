from pyspark.sql.functions import col, trim, upper, current_timestamp, lit, when, row_number
from pyspark.sql.window import Window

CATALOG = "data2026acc123456"
SCHEMA = "team3"

BRONZE_TABLE = f"{CATALOG}.{SCHEMA}.bronze_equipment"
SILVER_TABLE = f"{CATALOG}.{SCHEMA}.silver_equipment"
QUARANTINE_TABLE = f"{CATALOG}.{SCHEMA}.quarantine_equipment"

df_bronze = spark.read.table(BRONZE_TABLE)

# STANDARDIZATION
df_cleaned = (df_bronze
    # whitespace cleaning
    .withColumn("equipment_id", trim(col("equipment_id")))
    .withColumn("equipment_name", trim(col("equipment_name")))
    .withColumn("equipment_type", trim(col("equipment_type")))
    # Uppercase standardization
    .withColumn("country", upper(trim(col("country"))))
    .withColumn("site_code", upper(trim(col("site_code"))))
    .withColumn("criticality", upper(trim(col("criticality"))))
)


# DEDUPLICATION & SCD TYPE 1 LOGIC
window_spec = Window.partitionBy("equipment_id").orderBy(col("ingestion_timestamp").desc())

df_deduped = (df_cleaned
    .withColumn("rn", row_number().over(window_spec))
    .filter(col("rn") == 1)
    .drop("rn")
)

# DATA QUALITY RULES & QUARANTINE ROUTING
# null values, dates, power range and negative vibration values handling 
is_valid_condition = (
    col("equipment_id").isNotNull() & (col("equipment_id") != "") &  
    (col("decommission_date").isNull() | (col("decommission_date") >= col("install_date"))) & 
    (col("rated_power_kw").isNull() | ((col("rated_power_kw") >= 0) & (col("rated_power_kw") <= 5000))) & 
    (col("rated_vibration_max_mm_s").isNull() | (col("rated_vibration_max_mm_s") >= 0)) 
)

# dropping conditions
df_evaluated = df_deduped.withColumn(
    "is_valid", is_valid_condition
).withColumn(
    "quarantine_reason",
    when(col("equipment_id").isNull() | (col("equipment_id") == ""), lit("Missing equipment_id"))
    .when(col("decommission_date") < col("install_date"), lit("Decommission date before install date"))
    .when((col("rated_power_kw") < 0) | (col("rated_power_kw") > 5000), lit("Abnormal Rated Power (out of bounds)"))
    .when(col("rated_vibration_max_mm_s") < 0, lit("Negative Rated Vibration Limit"))
    .otherwise(lit(None))
)

# Silver vs Quarantine data
df_valid = df_evaluated.filter(col("is_valid") == True).drop("is_valid", "quarantine_reason")

df_quarantine = (df_evaluated
    .filter(col("is_valid") == False)
    .withColumn("quarantine_rule", lit("Equipment EDA Quality Checks"))
    .withColumn("quarantine_timestamp", current_timestamp())
    .drop("is_valid")
)

# SILVER & QUARANTINE
# overwright in silver table
df_valid.write.format("delta").mode("overwrite").option("mergeSchema", "true").saveAsTable(SILVER_TABLE)
valid_count = df_valid.count()
print(f"{valid_count} valid records in table: {SILVER_TABLE}")

# store Invalid Records in Quarantine Table. 
df_quarantine.write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable(QUARANTINE_TABLE)
quarantine_count = df_quarantine.count()
print(f"{quarantine_count} invalid records in table: {QUARANTINE_TABLE}")

print("silver process is done!")