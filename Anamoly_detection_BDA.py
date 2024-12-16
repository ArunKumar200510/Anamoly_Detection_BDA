# -*- coding: utf-8 -*-
"""Kmeans_email.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1YgI5L7rW6SnFtnGPYvb5SGG9zFG3xKrG

# Email Content: K-Means based Anomaly Detection

This notebook uses Colab to perform anomalous email content determination based on MinHash and K-Means algorithms via PySpark.

Data Source: [CERT Dataset](https://kilthub.cmu.edu/articles/dataset/Insider_Threat_Test_Dataset/12841247/1) from Carnegie Mellon University  

## Method overview

1. Split text to word list
2. Remove stop words
3. Generate word count vector
4. Reduce dimension by MinHash
5. Find the appropriate centroid for each obs by K-Means
6. Calculate its distance to the centroid
7. Sort to get the obs farthest from the corresponding centroid

## Build environment

Since Colab does not have PySpark module installed, we need to install PySpark and configure the related environment first.
"""

!pip install pyspark
!pip install -U -q PyDrive
!apt update
!apt install openjdk-8-jdk-headless -qq
import os
os.environ["JAVA_HOME"] = "/usr/lib/jvm/java-8-openjdk-amd64"

from google.colab import drive
drive.mount('/content/drive')

"""Please locate to the location where this notebook is saved."""

import os
cur_path = "/content/drive/MyDrive/Insider-Risk-in-PySpark/"
os.chdir(cur_path)
!pwd



"""Start Spark session."""

from pyspark.sql import SparkSession
spark = SparkSession.builder.appName('proj').getOrCreate()

spark

spark.sparkContext.getConf().getAll()

"""Import necessary modules."""

import matplotlib.pyplot as plt
import numpy as np

from pyspark.ml.feature import Tokenizer, StopWordsRemover, CountVectorizer, StandardScaler, MinHashLSH, VectorAssembler
from pyspark.sql.functions import udf, col
from pyspark.sql.types import *
from pyspark.ml.functions import vector_to_array
from pyspark.ml.linalg import Vectors

from pyspark.ml.clustering import KMeans
from pyspark.ml.evaluation import ClusteringEvaluator
from pyspark.mllib.stat import KernelDensity



"""## Load data

The **email.csv** file size is about 1GB and contains 2.6 million emails.
"""

email = spark.read.csv( './data/email.csv',inferSchema=True,header=True)

email.printSchema()
email.show(5)



"""## Extract word-vector

First, we need to split the email content into lists according to words, and then remove common meaningless words, aka "Stop words".
"""

tokenizer = Tokenizer(inputCol="content", outputCol="words")
wordsData = tokenizer.transform(email)

remover = StopWordsRemover(inputCol="words", outputCol="clean_words")
wordsData = remover.transform(wordsData)

wordsData.show()

"""`CountVectorizer` can convert a collection of text documents to vectors of token counts.
It can produces sparse representations for the documents over the vocabulary.

We choose 1000 as the vocabulary dimension under consideration. Of course, if the device allows, we can choose a larger dimension to obtain stronger representation ability.
"""

cv = CountVectorizer(inputCol="clean_words", outputCol="features", vocabSize=1000, minDF=2.0)

model = cv.fit(wordsData)

wordsCV = model.transform(wordsData)

"""Since the MinHash algorithm used in the later steps cannot handle the all-0 vector, we need to remove it in this step.
Of course, if the content of an email generates an all-0 vector as a result, it means that the content of that email is also anomalous. Therefore, the emails removed in this step also need to be treated as anomalous emails.
"""

all0vector = Vectors.dense([0]*1000)

# Filter the empty Sparse Vector
def no_empty_vector(value):
    if value != all0vector:
        return True
    else:
        return False


no_empty_vector_udf = udf(no_empty_vector, BooleanType())
wordsCV = wordsCV.filter(no_empty_vector_udf('features'))

wordsCV.show()

"""## Dimension reduction by MinHash

In this step, we reduce the dimensionality of the features used by using the MinHash algorithm, while ensuring that the similarity between the data is maintained. Also, this converts the sparse features into dense features.
"""

mh = MinHashLSH(inputCol="features", outputCol="hashes", numHashTables=20)
model = mh.fit(wordsCV)
wordsHash = model.transform(wordsCV)

wordsHash.show()

id_hash = wordsHash.select('id', 'hashes')

"""Since the features generated by the MinHashLSH function in Spark are a 20-dimensional list composed of each single-element DenseVector, we need to convert it to a flat 20-dimensional DenseVector.

Therefore, we first split the list into 20 columns, then convert the DenseVector in each column to a pure value, and finally merge the 20 columns.
"""

sc = spark.sparkContext

numAttrs = 20
attrs = sc.parallelize(["hash_" + str(i) for i in range(numAttrs)]).zipWithIndex().collect()
for name, index in attrs:
    id_hash = id_hash.withColumn(name, id_hash['hashes'].getItem(index))

id_hash.show()

udf_getNumber = udf(lambda x: int(x[0]), LongType())

for col_num in range(20):
    id_hash = id_hash.withColumn('hash_'+str(col_num), udf_getNumber('hash_'+str(col_num)))

id_hash.show()

hash_cols = ['hash_'+str(i) for i in range(20)]

assembler = VectorAssembler(inputCols=hash_cols, outputCol="features")
id_hash = assembler.transform(id_hash)

id_hash.show()

"""## Data rescale

At the same time, we can find that the values in the `features` we obtained are very large, which is not conducive to subsequent steps such as model training. Therefore, we need to use Scaler to scale them down to a suitable size.
"""

scaler = StandardScaler(inputCol="features", outputCol="scaledFeatures",
                        withStd=True, withMean=False)

# Compute summary statistics by fitting the StandardScaler
scalerModel = scaler.fit(id_hash)

# Normalize each feature to have unit standard deviation.
id_hash_scaled = scalerModel.transform(id_hash)
id_hash_scaled.show()

id_hash_scaled = id_hash_scaled.select('id','scaledFeatures')
id_hash_scaled.show()

hash_scaled = id_hash_scaled.select('scaledFeatures')
hash_scaled.show()

"""The amount of data was simply too large to be handled by Colab during the modeling process and caused a disconnection of its Java back-end server. Therefore, we only extract a portion of the data for demonstration."""

id_hash_sub = id_hash_scaled.sample(withReplacement=False, fraction=0.001, seed=42)

id_hash_sub_split = id_hash_sub.withColumn("scaledHash", vector_to_array("scaledFeatures")).select(['id'] + [col("scaledHash")[i] for i in range(20)])

id_hash_sub_split.show()

"""Also, the data was stored and then retrieved for manipulation to speed up the subsequent modeling process."""

id_hash_sub_split.write.csv('./data/id_hash_sub_split.csv', header = True, mode = 'error')



"""## Retrieve saved data

In this step, we need to retrieve the previously saved data and perform the modeling operation. If the runtime was ever interrupted after the previous step, you need to run the Build environment section at the beginning of the notebook.
"""

id_hash_sub_split = spark.read.csv( './data/id_hash_sub_split.csv',inferSchema=True,header=True)

id_hash_sub_split.show()

hash_cols = ['scaledHash['+str(i)+']' for i in range(20)]

assembler = VectorAssembler(inputCols=hash_cols, outputCol="scaledFeatures")
id_hash_sub = assembler.transform(id_hash_sub_split).select('id','scaledFeatures')

id_hash_sub.show()



"""## K-Means Modeling

Now we need to train the K-Means model. And determine the most suitable number of clustering categories.
"""

errors = []
results = []

for k in range(2,10):
    kmeansmodel = KMeans().setK(k).setMaxIter(10).setFeaturesCol('scaledFeatures').setPredictionCol('prediction').fit(id_hash_sub)

    print("With K={}".format(k))

    kmeans_results = kmeansmodel.transform(id_hash_sub)
    results.append(kmeans_results)

    # Evaluate clustering by computing Silhouette score
    evaluator = ClusteringEvaluator()
    evaluator.setFeaturesCol('scaledFeatures').setPredictionCol("prediction")

    silhouette = evaluator.evaluate(kmeans_results)
    errors.append(silhouette)
    print("Silhouette with squared euclidean distance = " + str(silhouette))

    print('--'*30 + '\n')

plt.figure()
k_number = range(2,10)
plt.plot(k_number,errors)
plt.xlabel('Number of K')
plt.ylabel('Silhouette')
plt.title('K - Silhouette')
plt.show()

"""Based on the variation of Silhouette with squared euclidean distance with k in the above figure, according to the elbow principle, we can consider 5 as the most appropriate number of categories that can bring the maximum classification gain with as few categories as possible."""

k = 5

kmeansmodel = KMeans().setK(k).setMaxIter(10).setFeaturesCol('scaledFeatures').setPredictionCol('prediction').fit(id_hash_sub)

kmeans_results = kmeansmodel.transform(id_hash_sub)

clusterCenters = kmeansmodel.clusterCenters()

kmeans_results.show()

"""Based on the clustering results obtained from the final model, the clustering of each data point to its corresponding category center is calculated.

The few data points farthest from their corresponding clustering centers are the anomalous emails we are looking for.
"""

df_list = []
for row in kmeans_results.collect():
    id = row['id']
    distance = np.linalg.norm(row['scaledFeatures'] - clusterCenters[row['prediction']])
    item = (id, row['scaledFeatures'],row['prediction'], str(distance))
    df_list.append(item)

rdd = sc.parallelize(df_list)
results = spark.createDataFrame(rdd,['id', 'scaledFeatures','prediction', 'distance'])

results = results.withColumn('distance', col('distance').cast(DoubleType()))
results = results.orderBy('distance', ascending=False)
results.show()

"""In addition, we can also get a more visualized range of the distance distribution by drawing the image of the KDE probability distribution for each distance. And with this, we can determine the appropriate distance threshold as the criterion for judging anomalies."""

distance = results.select('distance')
kd = KernelDensity()
kd.setSample(distance.rdd.map(lambda x: x[0]))

all_distance = list(np.arange(0,20,0.1))
prob_all_distance = kd.estimate(all_distance)

prob_max = max(prob_all_distance)
prob_min = min(prob_all_distance)


plt.plot(all_distance,prob_all_distance)
plt.xlim(0, 20)
plt.title("KDE Curve")
plt.show()



"""## Example of anomalous email

Based on the above steps, we obtain the list of emails sorted by anomaly degree.

For the obtained list of abnormal emails, we can take out the content of that email and review it.
"""

targetId = results.take(1)[0]['id']
targetId

targetEmail = email.where(col('id') == targetId)
targetEmail.show()

targetEmail.collect()[0]['content']

"""We can see that the first abnormal email in the list is an email containing non-sense content. This shows that our algorithm is really effective in finding anomalous emails in the huge volume of emails."""







"""P.S. In addition to the classical K-Means algorithm used in the previous section, the Bisecting KMeans algorithm described below can also be used as an alternative when we have high requirements on the running time of the algorithm."""

from pyspark.ml.clustering import BisectingKMeans
k = 2
bkm = BisectingKMeans().setK(k).setMaxIter(1).setFeaturesCol('scaledFeatures').setPredictionCol('prediction')
model = bkm.fit(id_hash_sub)

results = model.transform(id_hash_sub)

results.show()







