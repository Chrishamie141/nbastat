from __future__ import annotations
from backend.app.schemas.common import TeamSummary

NFL_TEAMS = [
 ('ARI','Arizona Cardinals','Arizona','Cardinals'),('ATL','Atlanta Falcons','Atlanta','Falcons'),('BAL','Baltimore Ravens','Baltimore','Ravens'),('BUF','Buffalo Bills','Buffalo','Bills'),('CAR','Carolina Panthers','Carolina','Panthers'),('CHI','Chicago Bears','Chicago','Bears'),('CIN','Cincinnati Bengals','Cincinnati','Bengals'),('CLE','Cleveland Browns','Cleveland','Browns'),('DAL','Dallas Cowboys','Dallas','Cowboys'),('DEN','Denver Broncos','Denver','Broncos'),('DET','Detroit Lions','Detroit','Lions'),('GB','Green Bay Packers','Green Bay','Packers'),('HOU','Houston Texans','Houston','Texans'),('IND','Indianapolis Colts','Indianapolis','Colts'),('JAX','Jacksonville Jaguars','Jacksonville','Jaguars'),('KC','Kansas City Chiefs','Kansas City','Chiefs'),('LV','Las Vegas Raiders','Las Vegas','Raiders'),('LAC','Los Angeles Chargers','Los Angeles','Chargers'),('LAR','Los Angeles Rams','Los Angeles','Rams'),('MIA','Miami Dolphins','Miami','Dolphins'),('MIN','Minnesota Vikings','Minnesota','Vikings'),('NE','New England Patriots','New England','Patriots'),('NO','New Orleans Saints','New Orleans','Saints'),('NYG','New York Giants','New York','Giants'),('NYJ','New York Jets','New York','Jets'),('PHI','Philadelphia Eagles','Philadelphia','Eagles'),('PIT','Pittsburgh Steelers','Pittsburgh','Steelers'),('SEA','Seattle Seahawks','Seattle','Seahawks'),('SF','San Francisco 49ers','San Francisco','49ers'),('TB','Tampa Bay Buccaneers','Tampa Bay','Buccaneers'),('TEN','Tennessee Titans','Tennessee','Titans'),('WSH','Washington Commanders','Washington','Commanders')]
NBA_TEAMS = [
 ('ATL','Atlanta Hawks','Atlanta','Hawks'),('BOS','Boston Celtics','Boston','Celtics'),('BKN','Brooklyn Nets','Brooklyn','Nets'),('CHA','Charlotte Hornets','Charlotte','Hornets'),('CHI','Chicago Bulls','Chicago','Bulls'),('CLE','Cleveland Cavaliers','Cleveland','Cavaliers'),('DAL','Dallas Mavericks','Dallas','Mavericks'),('DEN','Denver Nuggets','Denver','Nuggets'),('DET','Detroit Pistons','Detroit','Pistons'),('GS','Golden State Warriors','Golden State','Warriors'),('HOU','Houston Rockets','Houston','Rockets'),('IND','Indiana Pacers','Indiana','Pacers'),('LAC','LA Clippers','LA','Clippers'),('LAL','Los Angeles Lakers','Los Angeles','Lakers'),('MEM','Memphis Grizzlies','Memphis','Grizzlies'),('MIA','Miami Heat','Miami','Heat'),('MIL','Milwaukee Bucks','Milwaukee','Bucks'),('MIN','Minnesota Timberwolves','Minnesota','Timberwolves'),('NO','New Orleans Pelicans','New Orleans','Pelicans'),('NY','New York Knicks','New York','Knicks'),('OKC','Oklahoma City Thunder','Oklahoma City','Thunder'),('ORL','Orlando Magic','Orlando','Magic'),('PHI','Philadelphia 76ers','Philadelphia','76ers'),('PHX','Phoenix Suns','Phoenix','Suns'),('POR','Portland Trail Blazers','Portland','Trail Blazers'),('SAC','Sacramento Kings','Sacramento','Kings'),('SA','San Antonio Spurs','San Antonio','Spurs'),('TOR','Toronto Raptors','Toronto','Raptors'),('UTAH','Utah Jazz','Utah','Jazz'),('WSH','Washington Wizards','Washington','Wizards')]

def logo_url(league:str, abbr:str)->str:
    path='nfl' if league=='nfl' else 'nba'
    return f'https://a.espncdn.com/i/teamlogos/{path}/500/{abbr.lower()}.png'

def teams_for_league(league:str):
    league=league.lower(); rows=NFL_TEAMS if league=='nfl' else NBA_TEAMS if league=='nba' else []
    return [TeamSummary(id=abbr.lower(), league=league, name=name, city=city, nickname=nick, abbreviation=abbr, logoUrl=logo_url(league,abbr)) for abbr,name,city,nick in rows]

def team_by_abbreviation(league:str, abbr:str):
    ab=(abbr or '').upper()
    return next((t for t in teams_for_league(league) if t.abbreviation==ab), None)
