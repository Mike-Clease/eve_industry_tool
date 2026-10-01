import httpx
import csv
import os
import time
from config import EXTERNAL_DIR

FUZZWORKS_DATA = "https://www.fuzzwork.co.uk/dump/latest/csv"
IND_BLUEPRINTS = "industryBlueprints.csv"
IND_PRODUCTS = "industryActivityProducts.csv"
IND_PROBABILITIES = "industryActivityProbabilities.csv"
ITEM_TYPES = "invTypes.csv"
SOLAR_SYTEMS = "mapSolarSystems.csv"

DATA = [ITEM_TYPES, IND_BLUEPRINTS, IND_PRODUCTS, IND_PROBABILITIES, SOLAR_SYTEMS]


def download_csv(name, url=FUZZWORKS_DATA, path=EXTERNAL_DIR):
    r = httpx.get(f"{url}/{name}", timeout=60)
    r.raise_for_status()
    text = r.content.decode("utf-8-sig")
    csv_reader = csv.reader(text.splitlines())

    header = next(csv_reader)
    rows = list(csv_reader)

    destination = os.path.join(path, name)

    with open(destination, "w", newline="") as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow(header)
        csv_writer.writerows(rows)

    print(f"Successfully downloaded {name}")


def check_data_age(file, threshold: int = 86400):
    st = os.stat(os.path.join(EXTERNAL_DIR, file))
    mtime = int(st.st_mtime)
    epochtime = int(time.time())

    return (epochtime - mtime) > threshold


def update_data():
    for data in DATA:
        if os.path.isfile(os.path.join(EXTERNAL_DIR, data)):
            if check_data_age(data):
                download_csv(data)
            else:
                print("Data is current, skipping download.")
        else:
            download_csv(data)


if __name__ == "__main__":
    update_data()
