// Стабильные "пустые" значения без новых ссылок на каждом рендере
export const EMPTY_OBJ: Readonly<Record<string, never>> = Object.freeze({});
export const EMPTY_ARR: ReadonlyArray<never> = Object.freeze([]);
