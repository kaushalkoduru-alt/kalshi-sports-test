"""Alias tables for the big US leagues (venues name these teams differently: Kalshi uses cities, 'Los Angeles C';
Pinnacle uses full names; Polymarket US may use nicknames). Everything else goes through the strict fuzzy matcher."""

NBA = [
    ("Atlanta", "Hawks", "ATL"), ("Boston", "Celtics", "BOS"), ("Brooklyn", "Nets", "BKN"),
    ("Charlotte", "Hornets", "CHA"), ("Chicago", "Bulls", "CHI"), ("Cleveland", "Cavaliers", "CLE"),
    ("Dallas", "Mavericks", "DAL"), ("Denver", "Nuggets", "DEN"), ("Detroit", "Pistons", "DET"),
    ("Golden State", "Warriors", "GSW"), ("Houston", "Rockets", "HOU"), ("Indiana", "Pacers", "IND"),
    ("Los Angeles", "Clippers", "LAC"), ("Los Angeles", "Lakers", "LAL"), ("Memphis", "Grizzlies", "MEM"),
    ("Miami", "Heat", "MIA"), ("Milwaukee", "Bucks", "MIL"), ("Minnesota", "Timberwolves", "MIN"),
    ("New Orleans", "Pelicans", "NOP"), ("New York", "Knicks", "NYK"), ("Oklahoma City", "Thunder", "OKC"),
    ("Orlando", "Magic", "ORL"), ("Philadelphia", "76ers", "PHI"), ("Phoenix", "Suns", "PHX"),
    ("Portland", "Trail Blazers", "POR"), ("Sacramento", "Kings", "SAC"), ("San Antonio", "Spurs", "SAS"),
    ("Toronto", "Raptors", "TOR"), ("Utah", "Jazz", "UTA"), ("Washington", "Wizards", "WAS"),
]
NHL = [
    ("Anaheim", "Ducks", "ANA"), ("Boston", "Bruins", "BOS"), ("Buffalo", "Sabres", "BUF"),
    ("Calgary", "Flames", "CGY"), ("Carolina", "Hurricanes", "CAR"), ("Chicago", "Blackhawks", "CHI"),
    ("Colorado", "Avalanche", "COL"), ("Columbus", "Blue Jackets", "CBJ"), ("Dallas", "Stars", "DAL"),
    ("Detroit", "Red Wings", "DET"), ("Edmonton", "Oilers", "EDM"), ("Florida", "Panthers", "FLA"),
    ("Los Angeles", "Kings", "LAK"), ("Minnesota", "Wild", "MIN"), ("Montreal", "Canadiens", "MTL"),
    ("Nashville", "Predators", "NSH"), ("New Jersey", "Devils", "NJD"), ("New York", "Islanders", "NYI"),
    ("New York", "Rangers", "NYR"), ("Ottawa", "Senators", "OTT"), ("Philadelphia", "Flyers", "PHI"),
    ("Pittsburgh", "Penguins", "PIT"), ("San Jose", "Sharks", "SJS"), ("Seattle", "Kraken", "SEA"),
    ("St. Louis", "Blues", "STL"), ("Tampa Bay", "Lightning", "TBL"), ("Toronto", "Maple Leafs", "TOR"),
    ("Utah", "Mammoth", "UTA"), ("Vancouver", "Canucks", "VAN"), ("Vegas", "Golden Knights", "VGK"),
    ("Washington", "Capitals", "WSH"), ("Winnipeg", "Jets", "WPG"),
]
NFL = [
    ("Arizona", "Cardinals", "ARI"), ("Atlanta", "Falcons", "ATL"), ("Baltimore", "Ravens", "BAL"),
    ("Buffalo", "Bills", "BUF"), ("Carolina", "Panthers", "CAR"), ("Chicago", "Bears", "CHI"),
    ("Cincinnati", "Bengals", "CIN"), ("Cleveland", "Browns", "CLE"), ("Dallas", "Cowboys", "DAL"),
    ("Denver", "Broncos", "DEN"), ("Detroit", "Lions", "DET"), ("Green Bay", "Packers", "GB"),
    ("Houston", "Texans", "HOU"), ("Indianapolis", "Colts", "IND"), ("Jacksonville", "Jaguars", "JAX"),
    ("Kansas City", "Chiefs", "KC"), ("Las Vegas", "Raiders", "LV"), ("Los Angeles", "Chargers", "LAC"),
    ("Los Angeles", "Rams", "LAR"), ("Miami", "Dolphins", "MIA"), ("Minnesota", "Vikings", "MIN"),
    ("New England", "Patriots", "NE"), ("New Orleans", "Saints", "NO"), ("New York", "Giants", "NYG"),
    ("New York", "Jets", "NYJ"), ("Philadelphia", "Eagles", "PHI"), ("Pittsburgh", "Steelers", "PIT"),
    ("San Francisco", "49ers", "SF"), ("Seattle", "Seahawks", "SEA"), ("Tampa Bay", "Buccaneers", "TB"),
    ("Tennessee", "Titans", "TEN"), ("Washington", "Commanders", "WAS"),
]

# Kalshi series prefixes and Pinnacle league names that use these tables
TABLES = {"NBA": NBA, "NHL": NHL, "NFL": NFL}
