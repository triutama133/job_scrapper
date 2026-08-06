import os
import json
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, size, array_distinct, trim, when, regexp_replace, array_remove, transform, lower, split, flatten, array

def run_spark_transformation(filepath="jobs_data.json"):
    if not os.path.exists(filepath):
        print(f"Error: File {filepath} tidak ditemukan untuk transformasi Spark.")
        return False
        
    print("Menginisialisasi sesi Apache Spark lokal...")
    # Initialize a local Spark session
    spark = SparkSession.builder \
        .appName("JobMarketDataTransformation") \
        .master("local[*]") \
        .getOrCreate()
        
    try:
        print(f"Membaca {filepath} ke dalam Spark DataFrame...")
        # Read multiline JSON array
        df = spark.read.option("multiLine", "true").json(filepath)
        
        # Display initial schema for logging
        print("\n--- Schema Awal Spark DataFrame ---")
        df.printSchema()
        initial_count = df.count()
        print(f"Total baris awal: {initial_count}")
        
        # 1. Deduplikasi tingkat DataFrame berdasarkan job_url
        print("Menghapus lowongan kerja dengan URL terduplikasi...")
        df = df.dropDuplicates(["job_url"])
        
        # 2. Text Cleansing & Standardisasi menggunakan PySpark functions
        print("Membersihkan dan menormalisasi kolom teks...")
        df = df.withColumn("job_title", trim(col("job_title"))) \
               .withColumn("company", trim(regexp_replace(col("company"), r'\(NOT VERIFIED\)', ''))) \
               .withColumn("company", trim(col("company"))) \
               .withColumn("platform", trim(col("platform")))

        # 2.1 Membersihkan newline (\n) berlebih dari kolom deskripsi dan array
        print("Merapikan baris baru (newline) berlebih pada deskripsi...")
        if "description" in df.columns:
            df = df.withColumn("description", trim(regexp_replace(col("description"), r'\n+', '\n')))

        print("Pencegahan null dan pemisahan baris baru (newline) pada array requirements dan job_desk...")
        # Prevent null issues by coalescing to an empty array
        df = df.withColumn("job_desk", when(col("job_desk").isNull(), array()).otherwise(col("job_desk")))
        df = df.withColumn("requirements", when(col("requirements").isNull(), array()).otherwise(col("requirements")))

        # Split string elements containing \n and flatten
        df = df.withColumn("job_desk", flatten(transform(col("job_desk"), lambda x: split(x, r'\n'))))
        df = df.withColumn("requirements", flatten(transform(col("requirements"), lambda x: split(x, r'\n'))))

        # Trim whitespaces from elements
        df = df.withColumn("job_desk", transform(col("job_desk"), lambda x: trim(x)))
        df = df.withColumn("requirements", transform(col("requirements"), lambda x: trim(x)))
               
        # 3. Membersihkan nilai null/kosong di dalam list requirements & job_desk
        print("Membersihkan array requirements dan job_desk dari data kosong...")
        df = df.withColumn("requirements", array_distinct(col("requirements"))) \
               .withColumn("job_desk", array_distinct(col("job_desk")))
               
        # Remove empty strings from arrays if any
        df = df.withColumn("requirements", array_remove(col("requirements"), "")) \
               .withColumn("job_desk", array_remove(col("job_desk"), ""))
               
        # 4. Feature Engineering: Tambahkan kolom statistik data
        # Menghitung jumlah persyaratan dan deskripsi pekerjaan
        df = df.withColumn("requirements_count", size(col("requirements"))) \
               .withColumn("responsibilities_count", size(col("job_desk")))
               
        # Categorization logic using when().otherwise() based on job_title keywords
        title_lower = lower(col("job_title"))
        df = df.withColumn(
            "category",
            when(title_lower.rlike(r"data analyst|data science|data scientist|data engineer|analytics|machine learning|ml|ai|artificial intelligence|database|db|statistics|tableau|bi developer|power bi|pyspark|hadoop|data entry"), "Data & Analytics")
            .when(title_lower.rlike(r"software|developer|engineer|programmer|backend|frontend|fullstack|devops|sysadmin|network|cyber|cloud|it support|helpdesk|web|android|ios|mobile|php|java|python|react|node|system|coder|embedded"), "Technology & Software")
            .when(title_lower.rlike(r"design|graphic|creative|ui|ux|art|illustrator|designer|multimedia|animator|video editor|videographer|photographer|interior|copywriter|writer|draftsperson|creative director"), "Design & Creative")
            .when(title_lower.rlike(r"hr|recruitment|recruiter|human resource|human resources|talent acquisition|people ops|people operations|learning and development|l&d|hrbp|hrga|talent partner"), "Human Resources")
            .when(title_lower.rlike(r"finance|accounting|accountant|tax|audit|treasury|controller|billing|payroll|bookkeeper|underwriter|pricing|bank|banking|credit|loan|financial|auditor|investment"), "Finance & Accounting")
            .when(title_lower.rlike(r"marketing|sales|account executive|seo|sem|growth|ads|promotions|branding|content creator|social media|relations|public relation|pr|telemarketing|campaign|customer relations|salesperson|agent|partnership|business development"), "Marketing & Sales")
            .when(title_lower.rlike(r"doctor|nurse|medical|health|pharmacist|clinical|healthcare|physiotherapist|dentist|veterinarian"), "Healthcare & Medical")
            .when(title_lower.rlike(r"teacher|tutor|lecturer|pendidik|guru|dosen|instructor|academic|education|pengajar"), "Education & Teaching")
            .when(title_lower.rlike(r"operations|admin|administration|secretary|customer service|cs|support|helper|clerk|executive assistant|assistant|logistics|supply chain|project manager|pm|officer|manager|head|spv|supervisor|coordination|coordinator|chief|director"), "Operations & Admin")
            .otherwise("Others / General")
        )
               
        # Set default values for status markers if missing
        if "is_active" not in df.columns:
            df = df.withColumn("is_active", col("is_active"))
        else:
            df = df.withColumn("is_active", when(col("is_active").isNull(), True).otherwise(col("is_active")))
            
        final_count = df.count()
        print(f"Total baris setelah transformasi & deduplikasi: {final_count} (Tereduksi: {initial_count - final_count} baris)")
        
        # 5. Konversi Spark DataFrame kembali ke Python dictionary untuk penulisan file tunggal JSON
        print(f"Menulis data ter-transformasi kembali ke {filepath}...")
        # Collect to JSON strings and load as python dict
        json_rows = df.toJSON().collect()
        processed_jobs = [json.loads(row) for row in json_rows]
        
        # Write clean formatted JSON array
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(processed_jobs, f, indent=4, ensure_ascii=False)
            
        print("Transformasi PySpark selesai dengan sukses.")
        return True
        
    except Exception as e:
        print(f"Terjadi kesalahan saat pemrosesan PySpark: {e}")
        return False
    finally:
        # Always stop the Spark session to release JVM resources
        spark.stop()

if __name__ == "__main__":
    run_spark_transformation("jobs_data.json")
