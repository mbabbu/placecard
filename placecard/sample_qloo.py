"""SAMPLE DATA - NOT REAL QLOO RESULTS.

A hand-written catalogue used only when QLOO_API_KEY is not set, so the demo and the tests run
without a key. Film, music, book and show names are real public titles; places are invented;
every entity id starts with "SAMPLE-", every tag id with "urn:tag:sample:". The tag vectors are
computed here from hand-written tag lists (the first tag of an entity is its strongest), they are
NOT measured by Qloo. Results carry `sample: True` and the UI shows a SAMPLE banner.

SampleQloo mirrors the part of LiveQloo's interface that Placecard uses: search() and
taste_tags(). It is big enough for the two presets (a wedding and a conference dinner).
"""

# tag key -> display name. Tag ids are urn:tag:sample:<key>.
TAG_NAMES = {
    # story and genre
    "drama": "Drama", "comedy": "Comedy", "thriller": "Thriller", "horror": "Horror", "scifi": "Science Fiction",
    "fantasy": "Fantasy", "animation": "Animation", "romance": "Romance", "crime": "Crime & Mystery",
    "docu": "Documentary", "action": "Action", "musical": "Musical", "epic": "Epic Storytelling",
    "coming_of_age": "Coming of Age", "satire": "Satire", "dark_humor": "Dark Humor",
    # culture
    "k_culture": "Korean Culture", "j_culture": "Japanese Culture", "british": "British Culture",
    "latin": "Latin Culture", "malay": "Malaysian Culture", "french": "French Culture", "indian": "Indian Culture",
    # outlook and style
    "arthouse": "Arthouse", "indie": "Indie", "quirky": "Quirky", "feel_good": "Feel-Good", "nostalgic": "Nostalgia",
    "intellectual": "Big Ideas", "glam": "Glamour", "outdoors": "Outdoors & Adventure", "travel": "Travel",
    "fitness": "Fitness", "tech": "Technology", "startup": "Startups & Business", "gaming": "Gaming",
    "true_crime": "True Crime", "self_improve": "Self-Improvement", "history": "History",
    "literature": "Literary Fiction", "sustain": "Sustainability", "family": "Family Friendly",
    # music
    "pop": "Pop", "kpop": "K-Pop", "rock": "Rock", "indie_rock": "Indie Rock", "hiphop": "Hip-Hop", "rnb": "R&B",
    "jazz": "Jazz", "groove": "Funk & Groove", "electronic": "Electronic", "crooner": "Classic Crooners",
    "metal": "Metal", "latin_music": "Latin Music", "soundtrack": "Film Scores", "folk": "Folk",
    # food and places
    "street_food": "Street Food", "fine_dining": "Fine Dining", "vegetarian": "Vegetarian", "spicy": "Spicy Food",
    "seafood": "Seafood", "bbq": "BBQ & Grill", "baking": "Baking & Dessert", "coffee": "Coffee Culture",
    "craft_drinks": "Craft Drinks", "foodie": "Food Culture", "korean_food": "Korean Cuisine",
    "japanese_food": "Japanese Cuisine", "malay_food": "Malaysian Cuisine", "mexican_food": "Mexican Cuisine",
    "italian_food": "Italian Cuisine", "cozy": "Cozy", "lively": "Lively", "late_night": "Late Night",
}
TAG_ID = {k: f"urn:tag:sample:{k}" for k in TAG_NAMES}

# type code -> short type used by the client
T = {"M": "movie", "T": "tv_show", "A": "artist", "B": "book", "P": "podcast", "L": "place", "D": "destination",
     "R": "brand", "G": "video_game"}

# TYPE|Name|disambiguation|popularity|tags (strongest first)
_CATALOG = """
M|Parasite|2019, Bong Joon-ho|0.97|k_culture,thriller,dark_humor,arthouse,drama,satire
M|Parasite|1982, Charles Band|0.9|horror,scifi
M|Decision to Leave|2022, Park Chan-wook|0.64|k_culture,crime,arthouse,romance,thriller
M|Past Lives|2023, Celine Song|0.74|k_culture,romance,drama,indie,arthouse
M|Train to Busan|2016, Yeon Sang-ho|0.85|k_culture,horror,action,thriller
T|Squid Game|2021|0.96|k_culture,thriller,dark_humor,satire,drama
T|Crash Landing on You|2019|0.82|k_culture,romance,feel_good,drama
A|BTS||0.98|kpop,k_culture,pop,glam
A|NewJeans||0.84|kpop,k_culture,pop,electronic
L|Seoul Garden BBQ|Invented place, Bukit Bintang|0.6|korean_food,bbq,lively,k_culture,spicy
L|Jjim House|Invented place, Mont Kiara|0.5|korean_food,spicy,cozy,k_culture
M|Spirited Away|2001, Hayao Miyazaki|0.95|j_culture,animation,fantasy,family,coming_of_age
M|Your Name|2016, Makoto Shinkai|0.86|j_culture,animation,romance,fantasy,coming_of_age
M|Perfect Days|2023, Wim Wenders|0.57|j_culture,arthouse,drama,cozy,indie
A|Hikaru Utada||0.78|j_culture,pop,electronic,indie
A|Joe Hisaishi||0.69|j_culture,soundtrack,animation,cozy
B|Norwegian Wood|Haruki Murakami|0.7|j_culture,literature,romance,coming_of_age,nostalgic
T|Attack on Titan|2013|0.9|j_culture,animation,action,epic,dark_humor
L|Menya Kumo Ramen|Invented place, Bukit Bintang|0.7|japanese_food,late_night,cozy,j_culture,foodie
L|Sakura Sushi Bar|Invented place, KLCC|0.6|japanese_food,seafood,fine_dining,j_culture
A|Khruangbin||0.71|groove,indie,late_night,cozy,jazz
A|Tame Impala||0.9|indie_rock,electronic,indie,quirky,rock
A|Arctic Monkeys||0.88|indie_rock,rock,indie,late_night
A|Tom Misch||0.6|groove,jazz,indie,cozy,rnb
A|Phoebe Bridgers||0.72|indie,folk,indie_rock,coming_of_age,cozy
A|Radiohead||0.9|indie_rock,rock,intellectual,electronic,arthouse
M|The Grand Budapest Hotel|2014, Wes Anderson|0.9|quirky,comedy,indie,arthouse,nostalgic,british
M|Moonrise Kingdom|2012, Wes Anderson|0.7|quirky,indie,coming_of_age,romance,nostalgic
M|Lady Bird|2017, Greta Gerwig|0.75|coming_of_age,indie,comedy,drama,feel_good
M|Everything Everywhere All at Once|2022, Daniels|0.93|quirky,scifi,comedy,drama,indie,family
L|Vinyl & Matcha|Invented cafe, Bukit Bintang|0.52|coffee,indie,cozy,jazz,j_culture,quirky
L|Kopitiam Records Bar|Invented bar, Bangsar|0.55|craft_drinks,indie,late_night,lively,groove,malay
T|Chef's Table|2015|0.8|foodie,docu,fine_dining,travel,intellectual
T|Street Food|2019|0.7|street_food,foodie,docu,travel,spicy
T|The Bear|2022|0.85|foodie,drama,fine_dining,dark_humor,coming_of_age
M|Jiro Dreams of Sushi|2011|0.65|j_culture,docu,foodie,japanese_food,intellectual
M|Ratatouille|2007|0.88|animation,foodie,french,family,feel_good,fine_dining
B|Salt Fat Acid Heat|Samin Nosrat|0.6|foodie,baking,self_improve,fine_dining
B|Eat Pray Love|Elizabeth Gilbert|0.6|travel,foodie,romance,self_improve
T|Parts Unknown|2013|0.74|travel,foodie,docu,street_food,intellectual
L|Nasi Lemak Corner|Invented place, Kampung Baru|0.88|malay_food,street_food,spicy,lively,malay
L|Hawker Lane|Invented place, Penang|0.79|street_food,malay_food,lively,late_night,spicy
L|Trattoria Pagi|Invented place, Bangsar|0.55|italian_food,cozy,baking,family,fine_dining
L|Pastry Atelier|Invented place, Bangsar|0.5|baking,coffee,fine_dining,french,cozy
L|The Brew Society|Invented cafe, KL|0.58|coffee,craft_drinks,indie,cozy,tech
L|Daun Kitchen|Invented place, Bangsar|0.45|vegetarian,sustain,cozy,malay_food,foodie
M|Free Solo|2018|0.78|outdoors,docu,fitness,epic
M|The Secret Life of Walter Mitty|2013|0.7|travel,outdoors,feel_good,comedy,quirky
M|Into the Wild|2007|0.75|outdoors,travel,drama,literature,self_improve
T|Planet Earth|2006|0.9|docu,outdoors,sustain,travel,epic
B|Wild|Cheryl Strayed|0.65|outdoors,self_improve,travel,literature,fitness
B|Born to Run|Christopher McDougall|0.68|fitness,outdoors,self_improve,docu,history
B|Atomic Habits|James Clear|0.93|self_improve,startup,fitness,intellectual
R|Patagonia||0.8|outdoors,sustain,fitness,travel
R|Nike||0.95|fitness,glam,startup
D|Mount Kinabalu|Sabah, Malaysia|0.7|outdoors,fitness,travel,malay,epic
D|Langkawi|Kedah, Malaysia|0.75|travel,outdoors,malay,feel_good
D|Bali|Indonesia|0.9|travel,outdoors,feel_good,glam,sustain
D|Kyoto|Japan|0.9|travel,j_culture,history,foodie,cozy
D|Seoul|South Korea|0.9|travel,k_culture,foodie,kpop,lively
B|Sapiens|Yuval Noah Harari|0.95|history,intellectual,self_improve,docu
B|The Midnight Library|Matt Haig|0.8|literature,fantasy,self_improve,feel_good,romance
B|Pride and Prejudice|Jane Austen|0.9|romance,british,literature,history,nostalgic
B|Dune|Frank Herbert|0.9|scifi,epic,intellectual,fantasy,history
B|1984|George Orwell|0.9|scifi,intellectual,history,literature,satire
B|Normal People|Sally Rooney|0.7|literature,romance,drama,coming_of_age,indie
T|Succession|2018|0.9|drama,satire,dark_humor,startup,british
M|Oppenheimer|2023, Christopher Nolan|0.95|history,drama,intellectual,epic,scifi
M|Inception|2010, Christopher Nolan|0.93|scifi,thriller,intellectual,action,epic
M|Interstellar|2014, Christopher Nolan|0.92|scifi,epic,intellectual,drama,outdoors
T|Black Mirror|2011|0.9|scifi,satire,tech,thriller,dark_humor,british
B|Project Hail Mary|Andy Weir|0.85|scifi,tech,intellectual,feel_good,epic
A|Taylor Swift||0.99|pop,glam,romance,nostalgic,folk
A|Beyonce||0.98|pop,rnb,glam,hiphop,epic
A|Dua Lipa||0.9|pop,electronic,glam,lively
A|Bruno Mars||0.92|pop,groove,rnb,glam,feel_good
M|The Devil Wears Prada|2006|0.85|glam,comedy,drama,nostalgic,coming_of_age
M|Crazy Rich Asians|2018|0.8|glam,romance,comedy,feel_good,travel
M|Mamma Mia!|2008|0.8|musical,romance,feel_good,glam,travel
M|La La Land|2016, Damien Chazelle|0.88|musical,romance,jazz,drama,nostalgic
M|Barbie|2023, Greta Gerwig|0.9|comedy,glam,satire,feel_good,fantasy
T|Emily in Paris|2020|0.7|glam,romance,french,comedy,travel
A|Norah Jones||0.76|jazz,crooner,cozy,folk
A|Frank Sinatra||0.9|crooner,jazz,nostalgic,glam
A|Nat King Cole||0.78|crooner,jazz,nostalgic,romance
M|Casablanca|1942|0.85|romance,drama,history,nostalgic,crooner
M|The Sound of Music|1965|0.85|musical,family,feel_good,nostalgic,history
M|Roman Holiday|1953|0.6|romance,nostalgic,travel,comedy,glam
A|P. Ramlee||0.7|malay,nostalgic,crooner,musical,history
A|Yuna||0.62|malay,pop,cozy,indie,rnb
A|Sudirman||0.6|malay,pop,nostalgic,feel_good
M|Paddington 2|2017, Paul King|0.8|family,feel_good,british,comedy,quirky
T|Downton Abbey|2010|0.8|british,history,drama,romance,nostalgic
T|Ted Lasso|2020|0.9|feel_good,comedy,british,coming_of_age,drama
T|The Office|2005|0.95|comedy,satire,dark_humor,feel_good,nostalgic
T|Friends|1994|0.97|comedy,nostalgic,feel_good,romance
M|Finding Nemo|2003|0.93|animation,family,feel_good,travel,comedy
M|Coco|2017|0.9|animation,latin,family,musical,feel_good
M|Encanto|2021|0.85|animation,latin,musical,family,feel_good
T|Narcos|2015|0.85|latin,crime,thriller,true_crime,drama
A|Bad Bunny||0.97|latin_music,hiphop,latin,pop,lively
A|Kendrick Lamar||0.95|hiphop,intellectual,rnb,epic
A|Frank Ocean||0.85|rnb,indie,intellectual,arthouse,groove
A|SZA||0.86|rnb,pop,indie,glam
M|Spider-Man: Into the Spider-Verse|2018|0.9|animation,hiphop,action,quirky,coming_of_age
L|Taco Libre|Invented place, TTDI|0.5|mexican_food,lively,craft_drinks,latin,street_food
M|Knives Out|2019, Rian Johnson|0.88|crime,comedy,quirky,thriller,british
M|Gone Girl|2014, David Fincher|0.88|thriller,crime,dark_humor,drama
T|Mindhunter|2017|0.75|true_crime,crime,thriller,drama,intellectual
T|Making a Murderer|2015|0.7|true_crime,docu,crime,drama
P|Serial|2014|0.8|true_crime,crime,docu,intellectual
T|Sherlock|2010|0.85|crime,british,thriller,intellectual,quirky
M|Se7en|1995, David Fincher|0.88|thriller,crime,dark_humor,horror
A|Metallica||0.88|metal,rock,epic,fitness
A|Foo Fighters||0.85|rock,fitness,nostalgic,lively
A|Linkin Park||0.9|rock,metal,nostalgic,hiphop,gaming
M|Mad Max: Fury Road|2015|0.85|action,epic,scifi,fitness
M|John Wick|2014|0.85|action,crime,fitness,gaming
M|Top Gun: Maverick|2022|0.9|action,nostalgic,fitness,feel_good,epic
G|The Legend of Zelda: Breath of the Wild||0.95|gaming,outdoors,epic,fantasy,travel
G|Elden Ring||0.93|gaming,fantasy,epic,dark_humor
G|Hades||0.85|gaming,epic,indie,fantasy,satire
G|Stardew Valley||0.85|gaming,indie,cozy,feel_good,sustain
M|The Social Network|2010, David Fincher|0.9|tech,startup,drama,intellectual,dark_humor
T|Silicon Valley|2014|0.8|tech,startup,satire,comedy,dark_humor
T|Mr. Robot|2015|0.78|tech,thriller,intellectual,crime,dark_humor
P|Lex Fridman Podcast||0.85|tech,intellectual,self_improve,scifi,history
P|Acquired||0.7|startup,tech,history,intellectual,self_improve
P|Darknet Diaries||0.6|tech,true_crime,crime,intellectual
P|Radiolab||0.75|intellectual,docu,indie,quirky,history
P|Huberman Lab||0.85|self_improve,fitness,tech,intellectual
B|Zero to One|Peter Thiel|0.85|startup,tech,intellectual,self_improve
B|The Hard Thing About Hard Things|Ben Horowitz|0.7|startup,self_improve,tech,hiphop
B|Thinking, Fast and Slow|Daniel Kahneman|0.9|intellectual,self_improve,history,docu
B|Ender's Game|Orson Scott Card|0.8|scifi,gaming,epic,intellectual
B|The Pragmatic Programmer|Hunt and Thomas|0.6|tech,self_improve,startup,intellectual
B|Designing Data-Intensive Applications|Martin Kleppmann|0.55|tech,intellectual,startup
B|Educated|Tara Westover|0.8|self_improve,history,literature,drama,coming_of_age
B|The Alchemist|Paulo Coelho|0.9|travel,self_improve,fantasy,literature,latin
R|Apple||0.97|tech,startup,glam,indie
R|Notion||0.6|tech,startup,self_improve,indie
M|The Lord of the Rings: The Fellowship of the Ring|2001|0.95|fantasy,epic,british,outdoors,action
T|Game of Thrones|2011|0.97|fantasy,epic,drama,history,dark_humor
M|Dune: Part Two|2024|0.9|scifi,epic,action,intellectual,fantasy
M|Amelie|2001, Jean-Pierre Jeunet|0.8|french,quirky,romance,indie,feel_good
M|Before Sunrise|1995, Richard Linklater|0.7|romance,indie,travel,arthouse,nostalgic
M|Toy Story|1995|0.95|animation,family,feel_good,nostalgic,comedy
T|Brooklyn Nine-Nine|2013|0.88|comedy,feel_good,crime,quirky
M|Mrs. Doubtfire|1993|0.85|comedy,family,feel_good,nostalgic
A|Coldplay||0.97|pop,rock,feel_good,epic,lively
A|Ed Sheeran||0.96|pop,folk,romance,feel_good
A|Adele||0.96|pop,crooner,romance,epic,british
A|Daniel Caesar||0.75|rnb,indie,romance,cozy
A|Hans Zimmer||0.9|soundtrack,epic,scifi,intellectual
A|Ludovico Einaudi||0.7|soundtrack,cozy,intellectual,arthouse
A|Ravi Shankar||0.6|indian,history,jazz,intellectual
A|Arijit Singh||0.85|indian,romance,pop,feel_good
M|Dilwale Dulhania Le Jayenge|1995|0.75|indian,romance,musical,nostalgic,family
M|Lagaan|2001|0.65|indian,epic,history,musical,drama
L|Banana Leaf Corner|Invented place, Brickfields|0.7|spicy,indian,street_food,lively,vegetarian
L|Sunset Rooftop Bar|Invented bar, KL|0.6|craft_drinks,glam,lively,late_night
L|Smoke & Embers|Invented place, Damansara|0.55|bbq,craft_drinks,lively,foodie
L|Laut Seafood House|Invented place, Klang|0.6|seafood,malay_food,spicy,lively,family
"""

_W = [1.0, 0.86, 0.72, 0.6, 0.5, 0.42, 0.35]
CATALOG = []
for _i, _line in enumerate(_CATALOG.strip().splitlines(), 1):
    _t, _name, _dis, _pop, _tags = _line.split("|")
    CATALOG.append({"id": f"SAMPLE-{_i:03d}", "name": _name, "type": T[_t], "disambiguation": _dis,
                    "popularity": float(_pop), "tag_keys": _tags.split(",")})
BY_ID = {c["id"]: c for c in CATALOG}
NAMES = {c["name"].lower() for c in CATALOG}


def _entity(c):
    year = None
    for part in c["disambiguation"].split(","):
        part = part.strip()
        if part.isdigit() and len(part) == 4:
            year = int(part)
    return {"id": c["id"], "name": c["name"], "type": c["type"], "affinity": None, "popularity": c["popularity"],
            "disambiguation": c["disambiguation"], "year": year, "address": "", "description": "", "image": None,
            "tags": [TAG_NAMES[k] for k in c["tag_keys"]], "explain": {}}


def mock_taste_vector(entity_ids, take=40):
    """Stand-in for Qloo taste analysis: tag score = mean strength of the tag across the seeds,
    affinity = 0.25 + 0.7 * score. Returns {tag_id: {"name", "affinity"}}, strongest first."""
    seeds = [BY_ID[i] for i in dict.fromkeys(entity_ids) if i in BY_ID]
    if not seeds:
        return {}
    score = {}
    for s in seeds:
        for rank, key in enumerate(s["tag_keys"]):
            score[key] = score.get(key, 0.0) + _W[min(rank, len(_W) - 1)] / len(seeds)
    top = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))[:take]
    return {TAG_ID[k]: {"name": TAG_NAMES[k], "affinity": round(0.25 + 0.7 * v, 4)} for k, v in top}


class SampleQloo:
    sample = True

    def __init__(self):
        self.calls = 0

    def search(self, query, entity_type=None, take=5):
        self.calls += 1
        q = " ".join(query.lower().split())
        hits = [c for c in CATALOG if q and (q == c["name"].lower() or (len(q) > 3 and q in c["name"].lower()))
                and (entity_type is None or c["type"] == entity_type)]
        hits.sort(key=lambda c: (c["name"].lower() != q, -c["popularity"]))
        return [_entity(c) for c in hits[:take]]

    def taste_tags(self, entity_ids, take=40, tag_types=None):
        self.calls += 1
        return mock_taste_vector(list(entity_ids), take)
