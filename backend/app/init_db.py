from backend.app.database import initialize_auth_database, database_path

def main():
    initialize_auth_database()
    print(f'Initialized auth tables in {database_path()} without deleting existing data.')
if __name__ == '__main__': main()
