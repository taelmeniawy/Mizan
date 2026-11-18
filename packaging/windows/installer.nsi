Unicode true
RequestExecutionLevel user

!include "MUI2.nsh"
!include "x64.nsh"

!define UninstallKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\${AppId}"

Name "${ProductName}"
OutFile "${SetupPath}"
InstallDir "$LOCALAPPDATA\Programs\${AppId}"
InstallDirRegKey HKCU "${UninstallKey}" "InstallLocation"
VIProductVersion "${FileVersion}"
VIAddVersionKey "ProductName" "${ProductName}"
VIAddVersionKey "FileDescription" "Mizan Windows Setup"
VIAddVersionKey "FileVersion" "${Version}"
VIAddVersionKey "LegalCopyright" "Mizan"

!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Arabic"

LangString CloseMizan ${LANG_ARABIC} "أغلق ميزان ثم أعد المحاولة."
LangString ReplaceFailed ${LANG_ARABIC} "تعذر استبدال النسخة المثبتة من ميزان."
LangString InstallConflict ${LANG_ARABIC} "مجلد التثبيت يتعارض مع النسخة الحالية أو ملفات البيانات."
LangString InstallWriteFailed ${LANG_ARABIC} "تعذر حفظ ملفات التثبيت أو الإزالة في $INSTDIR."
LangString RegistrationFailed ${LANG_ARABIC} "تم نسخ ملفات ميزان، وتعذّر إكمال التسجيل في Windows. يمكنك تشغيل Mizan.exe أو Uninstall.exe من $INSTDIR."
LangString UnsupportedSystem ${LANG_ARABIC} "يتطلب ميزان إصدار Windows بنظام 64 بت."

Function .onInit
    ${IfNot} ${RunningX64}
        MessageBox MB_ICONSTOP "$(UnsupportedSystem)" /SD IDOK
        Abort
    ${EndIf}
FunctionEnd

Section "Mizan"
    ${If} $INSTDIR == "$LOCALAPPDATA\Mizan"
        MessageBox MB_ICONSTOP "$(InstallConflict)" /SD IDOK
        Abort
    ${EndIf}

    ReadRegStr $0 HKCU "${UninstallKey}" "InstallLocation"
    ${If} $0 != ""
    ${AndIf} $0 != $INSTDIR
        MessageBox MB_ICONSTOP "$(InstallConflict)" /SD IDOK
        Abort
    ${EndIf}

    ReadINIStr $2 "$INSTDIR\MizanInstall.ini" "Installation" "AppId"
    ${If} $0 != ""
    ${OrIf} $2 == "${AppId}"
        ${If} ${FileExists} "$INSTDIR\Uninstall.exe"
            ClearErrors
            ExecWait '"$INSTDIR\Uninstall.exe" /S _?=$INSTDIR' $1
            ${If} ${Errors}
            ${OrIf} $1 != 0
            ${OrIf} ${FileExists} "$INSTDIR\Mizan.exe"
                MessageBox MB_ICONSTOP "$(ReplaceFailed)" /SD IDOK
                Abort
            ${EndIf}
        ${EndIf}
    ${ElseIf} ${FileExists} "$INSTDIR\Mizan.exe"
    ${OrIf} ${FileExists} "$INSTDIR\Uninstall.exe"
    ${OrIf} ${FileExists} "$INSTDIR\MizanInstall.ini"
        MessageBox MB_ICONSTOP "$(InstallConflict)" /SD IDOK
        Abort
    ${EndIf}

    SetOutPath "$INSTDIR"
    File /r "${BundleDir}\*"
    ClearErrors
    WriteUninstaller "$INSTDIR\Uninstall.exe"
    WriteINIStr "$INSTDIR\MizanInstall.ini" "Installation" "AppId" "${AppId}"
    ${If} ${Errors}
        MessageBox MB_ICONSTOP "$(InstallWriteFailed)" /SD IDOK
        Abort
    ${EndIf}

    ClearErrors
    CreateShortcut "$SMPROGRAMS\${ProductName}.lnk" "$INSTDIR\Mizan.exe"
    WriteRegStr HKCU "${UninstallKey}" "DisplayName" "${ProductName}"
    WriteRegStr HKCU "${UninstallKey}" "DisplayVersion" "${Version}"
    WriteRegStr HKCU "${UninstallKey}" "DisplayIcon" '"$INSTDIR\Mizan.exe"'
    WriteRegStr HKCU "${UninstallKey}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "${UninstallKey}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegDWORD HKCU "${UninstallKey}" "NoModify" 1
    WriteRegDWORD HKCU "${UninstallKey}" "NoRepair" 1
    ${If} ${Errors}
        MessageBox MB_ICONEXCLAMATION "$(RegistrationFailed)" /SD IDOK
    ${EndIf}
SectionEnd

Section "Uninstall"
    ClearErrors
    Delete "$INSTDIR\Mizan.exe"
    ${If} ${FileExists} "$INSTDIR\Mizan.exe"
        MessageBox MB_ICONSTOP "$(CloseMizan)" /SD IDOK
        Abort
    ${EndIf}
    !include "${UninstallFiles}"
    Delete "$INSTDIR\MizanInstall.ini"
    Delete "$INSTDIR\Uninstall.exe"
    Delete "$SMPROGRAMS\${ProductName}.lnk"
    DeleteRegKey HKCU "${UninstallKey}"
    RMDir "$INSTDIR"
SectionEnd
