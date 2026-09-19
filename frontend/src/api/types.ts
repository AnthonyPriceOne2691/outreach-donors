/** Что отдаёт сервер. Имена полей повторяют схему API один в один:
 *  перевод на ходу — лишний слой, в котором опечатка видна не сразу. */

export type Role = 'admin' | 'operator';

/** Именованные действия. Ровно те же, что в матрице прав на сервере. */
export type Permission = 'view' | 'run' | 'settings' | 'send' | 'users';

export interface Me {
  id: number;
  email: string;
  role: Role;
  /** Что человек может на самом деле — роль вместе с точечными исключениями.
   *  Приходит готовым списком, чтобы фронт не повторял у себя матрицу прав
   *  и не разъезжался с ней: кнопка есть, а запрос отказывает. */
  permissions: Permission[];
  must_change_password: boolean;
  last_login_at: string | null;
}

export interface SignedIn {
  token: string;
  token_type: string;
  expires_in_hours: number;
  user: Me;
}

export interface UserCard extends Me {
  /** Чем права отличаются от роли. Отобрать обратно можно только это. */
  overrides: Partial<Record<Permission, boolean>>;
  is_active: boolean;
}

export interface OneTimePassword {
  user: UserCard;
  password: string;
  note: string;
}

export interface AccessPatch {
  role?: Role;
  is_active?: boolean;
  permissions?: Partial<Record<Permission, boolean>>;
}
