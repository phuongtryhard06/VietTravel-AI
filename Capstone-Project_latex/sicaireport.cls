\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{sicaireport}[2026/10/01 v1.0 SIC-AI Capstone Project Report Class]

% Base class
\LoadClass[a4paper,11pt,oneside]{report}

% Optional Vietnamese cover/font support; the default preserves the English form.
\newif\ifSICVietnamese
\SICVietnamesefalse
\DeclareOption{vietnamese}{\SICVietnamesetrue}
\ProcessOptions\relax

% Essential Packages
\RequirePackage{fontspec}
\ifSICVietnamese
  \IfFontExistsTF{Times New Roman}{
    \setmainfont{Times New Roman}
    \setsansfont{Arial}
    \setmonofont{Consolas}
  }{
    \setmainfont{Latin Modern Roman}
    \setsansfont{Latin Modern Sans}
    \setmonofont{Latin Modern Mono}
  }
\else
  \IfFontExistsTF{Latin Modern Roman}{
    \setmainfont{Latin Modern Roman}
    \setsansfont{Latin Modern Sans}
    \setmonofont{Latin Modern Mono}
  }{
    \setmainfont{Times New Roman}
    \setsansfont{Arial}
    \setmonofont{Consolas}
  }
\fi

\RequirePackage[
    a4paper,
    top=28mm,
    bottom=25mm,
    left=25mm,
    right=25mm,
    headheight=16pt,
    headsep=8mm,
    footskip=12mm
]{geometry}

\RequirePackage{xcolor}
% Official Samsung and Report Color Palette
\definecolor{SamsungBlue}{HTML}{193DB0}       % Exact primary blue from source docx (#193DB0)
\definecolor{SamsungDarkBlue}{HTML}{0D1B67}   % Deep corporate navy
\definecolor{SamsungLightBlue}{HTML}{DEE5FA}  % Exact table header background tint from docx (#DEE5FA)
\definecolor{SamsungGray}{HTML}{BFBFBF}       % Exact border/placeholder gray from docx (#BFBFBF)
\definecolor{SamsungDarkGray}{HTML}{333333}   % Body text accent / headers
\definecolor{SamsungLightGray}{HTML}{F8F9FA}  % Clean background gray
\definecolor{CodeBg}{HTML}{F5F7FA}           % Code background
\definecolor{CodeBorder}{HTML}{D0D7DE}       % Code frame

\RequirePackage{graphicx}
\RequirePackage{fancyhdr}
\RequirePackage{titlesec}
\RequirePackage{titletoc}
\RequirePackage{booktabs}
\RequirePackage{tabularx}
\RequirePackage{multirow}
\RequirePackage{colortbl}
\RequirePackage{caption}
\RequirePackage{listings}
\RequirePackage{amsmath}
\RequirePackage{amssymb}
\RequirePackage{enumitem}
\RequirePackage{setspace}

% Paragraph & Line Spacing
\setstretch{1.15}
\setlength{\parindent}{0pt}
\setlength{\parskip}{6pt plus 2pt minus 1pt}

% Caption Styling
\captionsetup{
    font={small,sf},
    labelfont={bf,color=SamsungBlue},
    labelsep=colon,
    skip=8pt
}
\captionsetup[table]{position=top,labelfont={bf,color=black}}
\captionsetup[figure]{position=bottom}

% ------------------------------------------------------------------------------
% CHAPTER BANNER FORMATTING
% Matches the exact full-width Samsung Blue banner shape from the Word template
% ------------------------------------------------------------------------------
\makeatletter
\newcommand{\chaptitlebox}[1]{%
  \noindent\colorbox{SamsungBlue}{%
    \parbox{\dimexpr\textwidth-2\fboxsep\relax}{%
      \vspace{5pt}%
      \hspace{8pt}\color{white}%
      \ifx\chaptername\appendixname
        \appendixname\ \thechapter.~#1%
      \else
        \thechapter.~#1%
      \fi
      \vspace{5pt}%
    }%
  }%
}

\newcommand{\chaptitleboxstar}[1]{%
  \noindent\colorbox{SamsungBlue}{%
    \parbox{\dimexpr\textwidth-2\fboxsep\relax}{%
      \vspace{5pt}%
      \hspace{8pt}\color{white}#1%
      \vspace{5pt}%
    }%
  }%
}
\makeatother

\titleformat{\chapter}[block]
  {\normalfont\sffamily\bfseries\Large}
  {}
  {0pt}
  {\chaptitlebox}
\titlespacing*{\chapter}{0pt}{0pt}{16pt}

\titleformat{name=\chapter,numberless}[block]
  {\normalfont\sffamily\bfseries\Large}
  {}
  {0pt}
  {\chaptitleboxstar}
\titlespacing*{name=\chapter,numberless}{0pt}{0pt}{16pt}

\titleformat{\section}
  {\normalfont\sffamily\large\bfseries\color{SamsungBlue}}
  {\thesection.}
  {0.6em}
  {}
\titlespacing*{\section}{0pt}{14pt plus 2pt minus 2pt}{6pt plus 1pt minus 1pt}

\titleformat{\subsection}
  {\normalfont\sffamily\normalsize\bfseries\color{SamsungDarkGray}}
  {\thesubsection.}
  {0.5em}
  {}
\titlespacing*{\subsection}{0pt}{10pt plus 2pt minus 2pt}{4pt plus 1pt minus 1pt}

\titleformat{\subsubsection}
  {\normalfont\sffamily\normalsize\color{SamsungDarkGray}}
  {\thesubsubsection.}
  {0.5em}
  {}
\titlespacing*{\subsubsection}{0pt}{8pt plus 2pt minus 2pt}{3pt plus 1pt minus 1pt}

% Table of Contents Styling
\titlecontents{chapter}[0pt]
  {\addvspace{10pt}\bfseries\sffamily\color{SamsungBlue}}
  {\thecontentslabel.\quad}
  {}
  {\hfill\contentspage}
  [\addvspace{2pt}]

\titlecontents{section}[1.8em]
  {\sffamily\color{SamsungDarkGray}}
  {\thecontentslabel.\quad}
  {}
  {\titlerule*[0.6pc]{.}\contentspage}

\titlecontents{subsection}[3.8em]
  {\sffamily\footnotesize\color{SamsungDarkGray}}
  {\thecontentslabel.\quad}
  {}
  {\titlerule*[0.6pc]{.}\contentspage}

% Header & Footer Styling
\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{\sffamily\footnotesize\color{SamsungDarkGray}\courseName}
\fancyhead[R]{\sffamily\footnotesize\bfseries\color{SamsungBlue}\className}
\fancyfoot[L]{\sffamily\footnotesize\color{SamsungDarkGray}\programName\ \textbar\ \reportType}
\fancyfoot[R]{\sffamily\footnotesize\bfseries\color{SamsungBlue}\thepage}
\renewcommand{\headrulewidth}{0.4pt}
\renewcommand{\footrulewidth}{0.4pt}
\renewcommand{\headrule}{\color{SamsungLightBlue}\hrule width\headwidth height 0.4pt \vskip-\headrulewidth}
\renewcommand{\footrule}{\color{SamsungLightBlue}\hrule width\headwidth height 0.4pt \vskip-\footrulewidth}

% Plain page style (used for chapter opening pages)
\fancypagestyle{plain}{
  \fancyhf{}
  \fancyfoot[L]{\sffamily\footnotesize\color{SamsungDarkGray}\programName\ \textbar\ \reportType}
  \fancyfoot[R]{\sffamily\footnotesize\bfseries\color{SamsungBlue}\thepage}
  \renewcommand{\headrulewidth}{0pt}
  \renewcommand{\footrulewidth}{0.4pt}
  \renewcommand{\footrule}{\color{SamsungLightBlue}\hrule width\headwidth height 0.4pt \vskip-\footrulewidth}
}

% Code Listing Styling
\lstdefinestyle{samsunglisting}{
    backgroundcolor=\color{CodeBg},
    commentstyle=\color{gray}\itshape,
    keywordstyle=\color{SamsungBlue}\bfseries,
    numberstyle=\tiny\color{gray}\ttfamily,
    stringstyle=\color{teal},
    basicstyle=\small\ttfamily,
    breakatwhitespace=false,
    breaklines=true,
    captionpos=b,
    keepspaces=true,
    numbers=left,
    numbersep=8pt,
    showspaces=false,
    showstringspaces=false,
    showtabs=false,
    tabsize=4,
    frame=single,
    rulecolor=\color{CodeBorder},
    xleftmargin=12pt,
    framexleftmargin=6pt
}
\lstset{style=samsunglisting}

% Cover text defaults can be overridden by language-specific metadata.
\providecommand{\coverTitleLineOne}{Capstone Project}
\providecommand{\coverTitleLineTwo}{Final Report}
\providecommand{\coverAudienceText}{For students (instructor review required)}
\providecommand{\coverClassLabel}{Class:}
\providecommand{\coverInstructorLabel}{Instructor:}
\providecommand{\coverMembersLabel}{Members:}

% Outer Cover Page (Page 1)
\newcommand{\makecoverpage}{%
  \begin{titlepage}
    \thispagestyle{empty}
    \vspace*{-12mm}
    % Samsung Logo
    \noindent
    \ifdefined\samsungLogoPath
      \includegraphics[height=1.4cm]{\samsungLogoPath}
    \else
      \includegraphics[height=1.4cm]{figures/samsung_logo.png}
    \fi
    
    \vspace{4.2cm}
    
    % Course Name
    \noindent{\fontsize{20pt}{24pt}\sffamily\color{black}\courseName}\par
    \vspace{0.8cm}
    
    % Capstone Project Final Report
    \noindent{\fontsize{34pt}{40pt}\sffamily\bfseries\color{SamsungBlue}\coverTitleLineOne}\par
    \vspace{0.2cm}
    \noindent{\fontsize{34pt}{40pt}\sffamily\bfseries\color{SamsungBlue}\coverTitleLineTwo}\par
    
    \vspace{1.2cm}
    % Subtitle
    \noindent{\fontsize{15pt}{18pt}\sffamily\color{black}\coverAudienceText}\par
    
    \vfill
    
    % Copyright Notice (Exact text from original docx)
    \noindent
    \begin{minipage}{\textwidth}
      \fontsize{7.5pt}{10.5pt}\sffamily\color{gray}
      \textcopyright2025 SAMSUNG. All rights reserved.\par
      Samsung Electronics Corporate Citizenship Office holds the copyright of this document.\par
      This document is a literary property protected by copyright law so reprint and reproduction without permission are prohibited.\par
      To use this document other than the curriculum of Samsung Innovation Campus, you must receive written consent from copyright holder.
    \end{minipage}
    \vspace*{-5mm}
  \end{titlepage}
  \clearpage
}

% Inner Detailed Title Page (Page 2)
\newcommand{\maketitledetailpage}{%
  \thispagestyle{empty}
  \vspace*{2cm}
  
  % Project Title Box
  \noindent
  \begin{center}
    \setlength{\fboxrule}{1.2pt}%
    \setlength{\fboxsep}{16pt}%
    \fcolorbox{SamsungGray}{white}{%
      \parbox{0.9\textwidth}{%
        \centering
        \fontsize{18pt}{24pt}\sffamily\bfseries\color{SamsungBlue}
        \projectTitle
      }%
    }
  \end{center}
  
  \vspace{2.5cm}
  
  % Date
  \begin{center}
    {\fontsize{14pt}{18pt}\sffamily\color{SamsungDarkGray}\projectDate}
  \end{center}
  
  \vspace{2.5cm}
  
  % Academic & Team Metadata Block
  \noindent
  \begin{minipage}[t]{0.48\textwidth}
    \vspace{0pt}
    {\sffamily\bfseries\color{SamsungBlue}\courseName}\par\vspace{2pt}
    {\sffamily\color{SamsungDarkGray}\programName}\par\vspace{10pt}
    
    {\sffamily\bfseries\coverClassLabel}\par\vspace{2pt}
    {\sffamily\color{SamsungDarkGray}\className}\par\vspace{10pt}
    
    {\sffamily\bfseries\coverInstructorLabel}\par\vspace{2pt}
    {\sffamily\color{SamsungDarkGray}\instructorName}\par
  \end{minipage}%
  \hfill
  \begin{minipage}[t]{0.48\textwidth}
    \vspace{0pt}
    \begin{flushright}
      {\fontsize{18pt}{22pt}\sffamily\bfseries\color{SamsungBlue}\teamName}\par
      \vspace{10pt}
      {\sffamily\bfseries\coverMembersLabel}\par
      \vspace{4pt}
      {\sffamily\color{SamsungDarkGray}\memberOne}\par
      {\sffamily\color{SamsungDarkGray}\memberTwo}\par
      \ifdefined\memberThree\ifx\memberThree\empty\else{\sffamily\color{SamsungDarkGray}\memberThree}\par\fi\fi
      \ifdefined\memberFour\ifx\memberFour\empty\else{\sffamily\color{SamsungDarkGray}\memberFour}\par\fi\fi
      \ifdefined\memberFive\ifx\memberFive\empty\else{\sffamily\color{SamsungDarkGray}\memberFive}\par\fi\fi
    \end{flushright}
  \end{minipage}
  
  \vfill
  \clearpage
}
