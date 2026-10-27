import { useSettings } from "./settings";

const en = {
  morning: "Morning", afternoon: "Afternoon", evening: "Evening", night: "Up late",
  placeholder: "Ask anything, or type / for commands", newChat: "New chat", incognito: "Incognito chat", incognitoNote: "Incognito: Panda won't remember this chat, and it's deleted when you leave it.", search: "Search",
  chats: "Chats", pinned: "Pinned", settings: "Settings", signOut: "Log out",
  start: "Start", stop: "Stop", starting: "Starting…", restarting: "Restarting…", stopping: "Saving…", running: "Online",
  offline: "Offline", queued: "Queued — Panda will answer when it's online", queuedBusy: "Waiting — Panda is finishing other chats first",
  thinking: "Thinking", thought: "Thought process", approve: "Approve", deny: "Deny",
  alwaysAllow: "Always allow", approvalNeeded: "Panda wants to use a tool",
  copy: "Copy", copied: "Copied", stopGen: "Stop", retry: "Retry", rename: "Rename", pin: "Pin",
  unpin: "Unpin", delete: "Delete", effort: "Effort", moreModels: "More models",
  notifications: "Notifications", empty: "No chats yet", noResults: "No results",
  credits: "Credits", creditsUnit: "credits", thisMonth: "this month", usage: "Usage",
  commandsPanda: "Panda", commandsCrew: "Kiro Crew", commandsShortcuts: "Shortcuts",
  live: "Live", needsYou: "Needs you", youControl: "You're in control", ended: "Ended",
  takeOver: "Take over", giveBack: "Give back to Panda", watching: "Watching Panda work",
  sideQuestion: "Side question", context: "Context",
};
type Dict = typeof en;

const it: Dict = {
  morning: "Buongiorno", afternoon: "Buon pomeriggio", evening: "Buonasera", night: "Ancora sveglio",
  placeholder: "Chiedi qualsiasi cosa, o scrivi / per i comandi", newChat: "Nuova chat", incognito: "Chat in incognito", incognitoNote: "Incognito: Panda non ricorderà questa chat, e verrà eliminata quando la lasci.", search: "Cerca",
  chats: "Chat", pinned: "Fissate", settings: "Impostazioni", signOut: "Esci",
  start: "Avvia", stop: "Ferma", starting: "Avvio…", restarting: "Riavvio…", stopping: "Salvataggio…", running: "Online",
  offline: "Offline", queued: "In coda — Panda risponderà appena online", queuedBusy: "In attesa — Panda sta finendo altre chat",
  thinking: "Sto pensando", thought: "Ragionamento", approve: "Approva", deny: "Rifiuta",
  alwaysAllow: "Consenti sempre", approvalNeeded: "Panda vuole usare uno strumento",
  copy: "Copia", copied: "Copiato", stopGen: "Ferma", retry: "Riprova", rename: "Rinomina", pin: "Fissa",
  unpin: "Sblocca", delete: "Elimina", effort: "Sforzo", moreModels: "Altri modelli",
  notifications: "Notifiche", empty: "Nessuna chat", noResults: "Nessun risultato",
  credits: "Crediti", creditsUnit: "crediti", thisMonth: "questo mese", usage: "Utilizzo",
  commandsPanda: "Panda", commandsCrew: "Kiro Crew", commandsShortcuts: "Scorciatoie",
  live: "Live", needsYou: "Serve il tuo aiuto", youControl: "Ora controlli tu", ended: "Terminata",
  takeOver: "Prendi il controllo", giveBack: "Restituisci a Panda", watching: "Guarda Panda al lavoro",
  sideQuestion: "Domanda a parte", context: "Contesto",
};

const uk: Dict = {
  morning: "Доброго ранку", afternoon: "Доброго дня", evening: "Добрий вечір", night: "Ще не спиш",
  placeholder: "Запитай будь-що або введи / для команд", newChat: "Новий чат", incognito: "Інкогніто-чат", incognitoNote: "Інкогніто: Panda не запам'ятає цей чат, і його буде видалено, коли ви вийдете.", search: "Пошук",
  chats: "Чати", pinned: "Закріплені", settings: "Налаштування", signOut: "Вийти",
  start: "Запуск", stop: "Стоп", starting: "Запуск…", restarting: "Перезапуск…", stopping: "Збереження…", running: "Онлайн",
  offline: "Офлайн", queued: "У черзі — Panda відповість, щойно буде онлайн", queuedBusy: "Чекає — Panda спершу завершує інші чати",
  thinking: "Думаю", thought: "Хід думок", approve: "Дозволити", deny: "Відхилити",
  alwaysAllow: "Завжди дозволяти", approvalNeeded: "Panda хоче використати інструмент",
  copy: "Копіювати", copied: "Скопійовано", stopGen: "Стоп", retry: "Повторити", rename: "Перейменувати",
  pin: "Закріпити", unpin: "Відкріпити", delete: "Видалити", effort: "Зусилля", moreModels: "Інші моделі",
  notifications: "Сповіщення", empty: "Ще немає чатів", noResults: "Нічого не знайдено",
  credits: "Кредити", creditsUnit: "кредитів", thisMonth: "цього місяця", usage: "Використання",
  commandsPanda: "Panda", commandsCrew: "Kiro Crew", commandsShortcuts: "Швидкі підказки",
  live: "Наживо", needsYou: "Потрібна допомога", youControl: "Керуєш ти", ended: "Завершено",
  takeOver: "Взяти керування", giveBack: "Повернути Panda", watching: "Дивись, як працює Panda",
  sideQuestion: "Питання збоку", context: "Контекст",
};

const DICTS = { en, it, uk };
export type Key = keyof Dict;

export function useT() {
  const { settings } = useSettings();
  const d = DICTS[settings.language] ?? en;
  return (k: Key) => d[k] ?? en[k];
}

export function greetingKey(): Key {
  const h = new Date().getHours();
  if (h < 5) return "night";
  if (h < 12) return "morning";
  if (h < 18) return "afternoon";
  return "evening";
}
